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
        self.register_with_prefix(path, display_name, size_bytes, "file")
    }

    pub fn register_runtime(
        &mut self,
        path: PathBuf,
        display_name: String,
        size_bytes: u64,
    ) -> SelectedImportFile {
        self.register_with_prefix(path, display_name, size_bytes, "runtime")
    }

    fn register_with_prefix(
        &mut self,
        path: PathBuf,
        display_name: String,
        size_bytes: u64,
        prefix: &str,
    ) -> SelectedImportFile {
        self.expire();
        let selected = SelectedImportFile {
            file_ref: format!("{}_{}", prefix, uuid::Uuid::new_v4().simple()),
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

    pub fn take_pair(&mut self, first: &str, second: &str) -> Result<(PathBuf, PathBuf), ()> {
        self.expire();
        if first == second
            || !self.entries.contains_key(first)
            || !self.entries.contains_key(second)
        {
            return Err(());
        }
        let first_path = self.entries.remove(first).ok_or(())?.path;
        let second_path = self.entries.remove(second).ok_or(())?.path;
        Ok((first_path, second_path))
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

    #[test]
    fn pair_consumption_is_atomic() {
        let mut registry = FileRegistry::default();
        let model = registry.register(
            PathBuf::from("/private/tmp/model.gguf"),
            "model.gguf".into(),
            12,
        );
        let manifest = registry.register(
            PathBuf::from("/private/tmp/manifest.json"),
            "manifest.json".into(),
            12,
        );
        assert!(registry.take_pair(&model.file_ref, "missing").is_err());
        assert_eq!(
            registry.take(&model.file_ref),
            Some(PathBuf::from("/private/tmp/model.gguf"))
        );
        let model = registry.register(
            PathBuf::from("/private/tmp/model.gguf"),
            "model.gguf".into(),
            12,
        );
        assert_eq!(
            registry.take_pair(&model.file_ref, &manifest.file_ref),
            Ok((
                PathBuf::from("/private/tmp/model.gguf"),
                PathBuf::from("/private/tmp/manifest.json")
            ))
        );
    }

    #[test]
    fn runtime_reference_has_dedicated_opaque_namespace() {
        let mut registry = FileRegistry::default();
        let path = PathBuf::from("/private/tmp/llama-cli");
        let selected = registry.register_runtime(path.clone(), "llama-cli".into(), 42);
        let serialized = serde_json::to_string(&selected).unwrap();
        assert!(selected.file_ref.starts_with("runtime_"));
        assert!(!serialized.contains("/private/tmp"));
        assert_eq!(registry.take(&selected.file_ref), Some(path));
    }
}
