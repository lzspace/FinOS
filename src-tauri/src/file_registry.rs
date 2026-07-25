use std::collections::HashMap;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use serde::Serialize;

const FILE_REF_TTL: Duration = Duration::from_secs(5 * 60);

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct SelectedImportFile {
    pub file_ref: String,
    pub display_name: String,
    pub size_bytes: u64,
}

#[derive(Debug, Clone)]
struct RegisteredFile {
    path: PathBuf,
    expires_at: Instant,
}

#[derive(Debug, Default)]
pub struct FileRegistry {
    entries: HashMap<String, RegisteredFile>,
}

impl FileRegistry {
    pub fn register(
        &mut self,
        path: PathBuf,
        display_name: String,
        size_bytes: u64,
    ) -> SelectedImportFile {
        self.expire();
        let selected = SelectedImportFile {
            file_ref: format!("file_{}", uuid::Uuid::new_v4().simple()),
            display_name,
            size_bytes,
        };
        self.entries.insert(
            selected.file_ref.clone(),
            RegisteredFile {
                path,
                expires_at: Instant::now() + FILE_REF_TTL,
            },
        );
        selected
    }

    pub fn take(&mut self, file_ref: &str) -> Option<PathBuf> {
        self.expire();
        self.entries.remove(file_ref).map(|entry| entry.path)
    }

    fn expire(&mut self) {
        let now = Instant::now();
        self.entries.retain(|_, entry| entry.expires_at > now);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reference_is_opaque_one_time_and_contains_no_path() {
        let mut registry = FileRegistry::default();
        let path = PathBuf::from("/private/tmp/private-name.csv");
        let selected = registry.register(path.clone(), "Monat.csv".into(), 12);
        let serialized = serde_json::to_string(&selected).unwrap();
        assert!(selected.file_ref.starts_with("file_"));
        assert!(!serialized.contains("private-name"));
        assert!(!serialized.contains("/private"));
        assert_eq!(registry.take(&selected.file_ref), Some(path));
        assert_eq!(registry.take(&selected.file_ref), None);
    }
}
