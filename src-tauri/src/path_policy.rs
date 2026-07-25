use std::fs;
use std::path::{Path, PathBuf};

use thiserror::Error;

pub const DEFAULT_MAX_IMPORT_BYTES: u64 = 50 * 1024 * 1024;

#[derive(Debug, Error, PartialEq, Eq)]
pub enum PathPolicyError {
    #[error("FINANCE_IMPORT_FILE_SYMLINK_REJECTED")]
    Symlink,
    #[error("FINANCE_IMPORT_FILE_NOT_REGULAR")]
    NotRegular,
    #[error("FINANCE_IMPORT_FILE_NOT_CSV")]
    NotCsv,
    #[error("FINANCE_IMPORT_FILE_TOO_LARGE")]
    TooLarge,
    #[error("FINANCE_IMPORT_FILE_REPOSITORY_PATH")]
    Repository,
    #[error("FINANCE_IMPORT_FILE_CLOUD_PATH")]
    Cloud,
    #[error("FINANCE_IMPORT_FILE_NETWORK_PATH")]
    Network,
    #[error("FINANCE_IMPORT_FILE_STORE_PATH")]
    Store,
    #[error("FINANCE_IMPORT_FILE_UNREADABLE")]
    Unreadable,
}

#[derive(Clone, Debug)]
pub struct PathPolicy {
    repository_root: PathBuf,
    store_root: PathBuf,
    max_bytes: u64,
}

impl PathPolicy {
    pub fn new(repository_root: PathBuf, store_root: PathBuf, max_bytes: u64) -> Self {
        Self {
            repository_root,
            store_root,
            max_bytes,
        }
    }

    pub fn validate_csv(&self, input: &Path) -> Result<(PathBuf, u64), PathPolicyError> {
        reject_symlink_component(input)?;
        let canonical = input
            .canonicalize()
            .map_err(|_| PathPolicyError::Unreadable)?;
        let metadata = fs::metadata(&canonical).map_err(|_| PathPolicyError::Unreadable)?;
        if !metadata.file_type().is_file() {
            return Err(PathPolicyError::NotRegular);
        }
        if canonical
            .extension()
            .and_then(|value| value.to_str())
            .map(|value| value.eq_ignore_ascii_case("csv"))
            != Some(true)
        {
            return Err(PathPolicyError::NotCsv);
        }
        if metadata.len() > self.max_bytes {
            return Err(PathPolicyError::TooLarge);
        }
        if is_within(&canonical, &self.repository_root) {
            return Err(PathPolicyError::Repository);
        }
        if is_within(&canonical, &self.store_root) {
            return Err(PathPolicyError::Store);
        }
        let normalized = canonical.to_string_lossy().to_ascii_lowercase();
        const CLOUD_MARKERS: &[&str] = &[
            "/library/mobile documents/",
            "/library/cloudstorage/",
            "/icloud drive/",
            "/dropbox/",
            "/onedrive",
            "/google drive/",
            "/nextcloud/",
            "/syncthing/",
        ];
        if CLOUD_MARKERS
            .iter()
            .any(|marker| normalized.contains(marker))
        {
            return Err(PathPolicyError::Cloud);
        }
        if normalized.starts_with("/volumes/") || is_nonlocal_volume(&canonical) {
            return Err(PathPolicyError::Network);
        }
        Ok((canonical, metadata.len()))
    }
}

fn is_within(path: &Path, root: &Path) -> bool {
    root.canonicalize()
        .map(|root| path.starts_with(root))
        .unwrap_or_else(|_| path.starts_with(root))
}

fn reject_symlink_component(input: &Path) -> Result<(), PathPolicyError> {
    match fs::symlink_metadata(input) {
        Ok(metadata) if metadata.file_type().is_symlink() => Err(PathPolicyError::Symlink),
        Ok(_) => Ok(()),
        Err(_) => Err(PathPolicyError::Unreadable),
    }
}

#[cfg(target_os = "macos")]
fn is_nonlocal_volume(path: &Path) -> bool {
    use std::ffi::CString;
    use std::mem::MaybeUninit;
    use std::os::unix::ffi::OsStrExt;

    let Ok(c_path) = CString::new(path.as_os_str().as_bytes()) else {
        return true;
    };
    let mut stats = MaybeUninit::<libc::statfs>::uninit();
    // SAFETY: c_path is NUL terminated and stats points to valid writable memory.
    let result = unsafe { libc::statfs(c_path.as_ptr(), stats.as_mut_ptr()) };
    if result != 0 {
        return true;
    }
    // SAFETY: statfs returned success and initialized stats.
    let stats = unsafe { stats.assume_init() };
    (stats.f_flags as u64 & libc::MNT_LOCAL as u64) == 0
}

#[cfg(not(target_os = "macos"))]
fn is_nonlocal_volume(_path: &Path) -> bool {
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn fixture() -> (tempfile::TempDir, PathPolicy) {
        let root = tempfile::tempdir().unwrap();
        let repository = root.path().join("repo");
        let store = root.path().join("store");
        fs::create_dir_all(&repository).unwrap();
        fs::create_dir_all(&store).unwrap();
        (
            root,
            PathPolicy::new(repository, store, DEFAULT_MAX_IMPORT_BYTES),
        )
    }

    #[test]
    fn accepts_one_regular_csv_and_reports_size() {
        let (root, policy) = fixture();
        let path = root.path().join("statement.csv");
        fs::write(&path, b"synthetic;only\n").unwrap();
        let (resolved, size) = policy.validate_csv(&path).unwrap();
        assert_eq!(resolved, path.canonicalize().unwrap());
        assert_eq!(size, 15);
    }

    #[test]
    fn rejects_repository_store_non_csv_and_oversize_paths() {
        let (root, policy) = fixture();
        let repo_file = root.path().join("repo/fixture.csv");
        fs::write(&repo_file, b"x").unwrap();
        assert_eq!(
            policy.validate_csv(&repo_file),
            Err(PathPolicyError::Repository)
        );

        let store_file = root.path().join("store/fixture.csv");
        fs::write(&store_file, b"x").unwrap();
        assert_eq!(
            policy.validate_csv(&store_file),
            Err(PathPolicyError::Store)
        );

        let other = root.path().join("fixture.txt");
        fs::write(&other, b"x").unwrap();
        assert_eq!(policy.validate_csv(&other), Err(PathPolicyError::NotCsv));

        let large = root.path().join("large.csv");
        let mut handle = fs::File::create(&large).unwrap();
        handle.write_all(b"12345").unwrap();
        let strict = PathPolicy::new(
            root.path().join("elsewhere"),
            root.path().join("another-store"),
            4,
        );
        assert_eq!(strict.validate_csv(&large), Err(PathPolicyError::TooLarge));
    }

    #[test]
    fn rejects_cloud_sync_and_non_regular_paths() {
        let (root, policy) = fixture();
        let cloud = root
            .path()
            .join("Library/CloudStorage/Provider/statement.csv");
        fs::create_dir_all(cloud.parent().unwrap()).unwrap();
        fs::write(&cloud, b"x").unwrap();
        assert_eq!(policy.validate_csv(&cloud), Err(PathPolicyError::Cloud));

        let directory = root.path().join("directory.csv");
        fs::create_dir(&directory).unwrap();
        assert_eq!(
            policy.validate_csv(&directory),
            Err(PathPolicyError::NotRegular)
        );
    }

    #[cfg(unix)]
    #[test]
    fn rejects_symlink_instead_of_following_it() {
        use std::os::unix::fs::symlink;
        let (root, policy) = fixture();
        let original = root.path().join("original.csv");
        let link = root.path().join("linked.csv");
        fs::write(&original, b"x").unwrap();
        symlink(&original, &link).unwrap();
        assert_eq!(policy.validate_csv(&link), Err(PathPolicyError::Symlink));
    }
}
