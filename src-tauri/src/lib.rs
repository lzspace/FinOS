mod file_registry;
mod path_policy;
mod protocol;
mod python_client;

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use file_registry::{FileRegistry, SelectedImportFile};
use path_policy::{PathPolicy, DEFAULT_MAX_IMPORT_BYTES};
use protocol::ResponseEnvelope;
use python_client::PythonClient;
use serde_json::{json, Value};
use tauri::{Manager, WebviewWindowBuilder};
use tauri_plugin_dialog::{DialogExt, FilePath};

const INITIALIZATION_SCRIPT: &str = r#"
(() => {
  const core = window.__TAURI__.core;
  const bridge = Object.freeze({
    selectImportFile: () => core.invoke("select_import_file"),
    invoke: (operation, payload) => core.invoke("finance_invoke", { operation, payload }),
    getRuntimeStatus: () => core.invoke("get_runtime_status")
  });
  Object.defineProperty(window, "__FINANCE_IPC__", {
    value: bridge,
    configurable: false,
    enumerable: false,
    writable: false
  });
})();
"#;

struct DesktopState {
    files: Mutex<FileRegistry>,
    path_policy: PathPolicy,
    application: Mutex<PythonClient>,
}

#[derive(Debug, thiserror::Error)]
enum HostError {
    #[error("{0}")]
    SafeCode(String),
}

impl serde::Serialize for HostError {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        serializer.serialize_str(&self.to_string())
    }
}

impl From<python_client::PythonClientError> for HostError {
    fn from(value: python_client::PythonClientError) -> Self {
        Self::SafeCode(value.to_string())
    }
}

#[tauri::command]
async fn select_import_file(
    app: tauri::AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<Option<SelectedImportFile>, HostError> {
    // Use the callback picker all the way through. Even inside an async Tauri
    // command, blocking_pick_file can occupy the macOS event-loop thread while
    // NSOpenPanel is navigating folders.
    let (sender, receiver) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .set_title("CSV-Bankexport auswählen")
        .add_filter("CSV-Datei", &["csv"])
        .pick_file(move |selected| {
            let _ = sender.send(selected);
        });
    let selected = receiver
        .await
        .map_err(|_| HostError::SafeCode("FINANCE_IMPORT_FILE_DIALOG_FAILED".into()))?;
    let Some(selected) = selected else {
        return Ok(None);
    };
    let path = match selected {
        FilePath::Path(path) => path,
        _ => {
            return Err(HostError::SafeCode(
                "FINANCE_IMPORT_FILE_NOT_REGULAR".into(),
            ))
        }
    };
    let (canonical, size_bytes) = state
        .path_policy
        .validate_csv(&path)
        .map_err(|error| HostError::SafeCode(error.to_string()))?;
    let display_name = canonical
        .file_name()
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| HostError::SafeCode("FINANCE_IMPORT_FILE_UNREADABLE".into()))?
        .to_owned();
    Ok(Some(
        state
            .files
            .lock()
            .map_err(|_| HostError::SafeCode("DESKTOP_STATE_UNAVAILABLE".into()))?
            .register(canonical, display_name, size_bytes),
    ))
}

#[tauri::command]
fn finance_invoke(
    operation: String,
    mut payload: Value,
    state: tauri::State<'_, DesktopState>,
) -> Result<ResponseEnvelope, HostError> {
    if operation != "query" && operation != "command" {
        return Err(HostError::SafeCode("DESKTOP_OPERATION_UNSUPPORTED".into()));
    }

    if operation == "command" {
        let mut registry = state
            .files
            .lock()
            .map_err(|_| HostError::SafeCode("DESKTOP_STATE_UNAVAILABLE".into()))?;
        resolve_import_reference(&mut payload, &mut registry)?;
    }

    let response = state
        .application
        .lock()
        .map_err(|_| HostError::SafeCode("DESKTOP_APPLICATION_PROCESS_UNAVAILABLE".into()))?
        .send(&operation, payload)?;
    Ok(response)
}

fn resolve_import_reference(
    payload: &mut Value,
    registry: &mut FileRegistry,
) -> Result<(), HostError> {
    let Some(command) = payload.get("name").and_then(Value::as_str) else {
        return Ok(());
    };
    if command != "AnalyzeImportFile" && command != "ImportTransactions" {
        return Ok(());
    }
    let body = payload
        .get_mut("payload")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| HostError::SafeCode("DESKTOP_OPERATION_PAYLOAD_INVALID".into()))?;
    let file_ref = body
        .remove("source_file_reference")
        .and_then(|value| value.as_str().map(ToOwned::to_owned))
        .ok_or_else(|| HostError::SafeCode("FINANCE_IMPORT_FILE_REFERENCE_INVALID".into()))?;
    let path = registry
        .take(&file_ref)
        .ok_or_else(|| HostError::SafeCode("FINANCE_IMPORT_FILE_REFERENCE_INVALID".into()))?;
    body.remove("source_file_path");
    body.insert(
        "source_file_path".into(),
        Value::String(path.to_string_lossy().into_owned()),
    );
    Ok(())
}

#[tauri::command]
fn get_runtime_status(
    state: tauri::State<'_, DesktopState>,
) -> Result<ResponseEnvelope, HostError> {
    state
        .application
        .lock()
        .map_err(|_| HostError::SafeCode("DESKTOP_APPLICATION_PROCESS_UNAVAILABLE".into()))?
        .send("runtime_status", json!({}))
        .map_err(Into::into)
}

#[cfg(feature = "e2e-fixtures")]
#[tauri::command]
fn register_test_fixture(
    fixture_name: String,
    state: tauri::State<'_, DesktopState>,
) -> Result<SelectedImportFile, HostError> {
    if fixture_name.contains('/') || fixture_name.contains('\\') || !fixture_name.ends_with(".csv")
    {
        return Err(HostError::SafeCode("E2E_FIXTURE_INVALID".into()));
    }
    let path = repository_root()
        .join("extensions/finance/tests/fixtures/synthetic")
        .join(fixture_name);
    let metadata = std::fs::metadata(&path)
        .map_err(|_| HostError::SafeCode("E2E_FIXTURE_NOT_FOUND".into()))?;
    if !metadata.is_file() {
        return Err(HostError::SafeCode("E2E_FIXTURE_NOT_FOUND".into()));
    }
    let display_name = path
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("synthetic.csv")
        .to_owned();
    let fixture_root = std::env::temp_dir().join("agent-os-finance-e2e");
    std::fs::create_dir_all(&fixture_root)
        .map_err(|_| HostError::SafeCode("E2E_FIXTURE_COPY_FAILED".into()))?;
    let copied = fixture_root.join(format!(
        "{}-{}",
        uuid::Uuid::new_v4().simple(),
        display_name
    ));
    std::fs::copy(&path, &copied)
        .map_err(|_| HostError::SafeCode("E2E_FIXTURE_COPY_FAILED".into()))?;
    Ok(state
        .files
        .lock()
        .map_err(|_| HostError::SafeCode("DESKTOP_STATE_UNAVAILABLE".into()))?
        .register(copied, display_name, metadata.len()))
}

fn repository_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri must be inside the repository")
        .to_path_buf()
}

fn configured_worker() -> (PathBuf, Vec<String>) {
    if let Some(program) = std::env::var_os("FINANCE_DESKTOP_WORKER") {
        return (PathBuf::from(program), Vec::new());
    }
    if let Some(python) = std::env::var_os("FINANCE_PYTHON_EXECUTABLE") {
        return (
            PathBuf::from(python),
            vec!["-m".into(), "finance_extension.desktop_worker".into()],
        );
    }
    if let Ok(executable) = std::env::current_exe() {
        if let Some(directory) = executable.parent() {
            let bundled = directory.join("finance-desktop-worker");
            if bundled.is_file() {
                return (bundled, Vec::new());
            }
        }
    }
    (PathBuf::from("finance-desktop-worker"), Vec::new())
}

fn data_dir(app: &tauri::AppHandle) -> Result<PathBuf, Box<dyn std::error::Error>> {
    if let Some(override_path) = std::env::var_os("FINANCE_DESKTOP_DATA_DIR") {
        return Ok(PathBuf::from(override_path));
    }
    Ok(app.path().app_local_data_dir()?.join("store"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let mut builder = tauri::Builder::default().plugin(tauri_plugin_dialog::init());

    #[cfg(feature = "e2e-fixtures")]
    {
        builder = builder.invoke_handler(tauri::generate_handler![
            select_import_file,
            finance_invoke,
            get_runtime_status,
            register_test_fixture
        ]);
    }
    #[cfg(not(feature = "e2e-fixtures"))]
    {
        builder = builder.invoke_handler(tauri::generate_handler![
            select_import_file,
            finance_invoke,
            get_runtime_status
        ]);
    }

    builder
        .setup(|app| {
            let store_root = data_dir(app.handle())?;
            let max_bytes = std::env::var("FINANCE_MAX_IMPORT_BYTES")
                .ok()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(DEFAULT_MAX_IMPORT_BYTES);
            let (worker, mut worker_args) = configured_worker();
            worker_args.extend([
                "--data-dir".into(),
                store_root.to_string_lossy().into_owned(),
            ]);
            if cfg!(feature = "e2e-fixtures")
                && std::env::var("FINANCE_DESKTOP_E2E").as_deref() == Ok("1")
            {
                worker_args.push("--e2e-test-key-env".into());
            }
            let application = PythonClient::spawn(&worker, &worker_args)?;
            app.manage(DesktopState {
                files: Mutex::new(FileRegistry::default()),
                path_policy: PathPolicy::new(repository_root(), store_root, max_bytes),
                application: Mutex::new(application),
            });

            let window_config = app
                .config()
                .app
                .windows
                .first()
                .ok_or("main window configuration missing")?
                .clone();
            WebviewWindowBuilder::from_config(app, &window_config)?
                .initialization_script(INITIALIZATION_SCRIPT)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("Agent OS Finance desktop host could not start");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_opaque_reference_only_inside_host_and_consumes_it() {
        let mut registry = FileRegistry::default();
        let selected = registry.register(
            PathBuf::from("/private/tmp/synthetic.csv"),
            "synthetic.csv".into(),
            12,
        );
        let mut payload = json!({
            "name": "AnalyzeImportFile",
            "payload": {
                "source_file_reference": selected.file_ref,
                "source_file_path": "/untrusted/ui/path.csv",
                "requested_profile": "GermanMultiAccountCsvV1"
            }
        });
        resolve_import_reference(&mut payload, &mut registry).unwrap();
        let body = payload["payload"].as_object().unwrap();
        assert_eq!(
            body.get("source_file_path"),
            Some(&Value::String("/private/tmp/synthetic.csv".into()))
        );
        assert!(!body.contains_key("source_file_reference"));

        let mut replay = json!({
            "name": "AnalyzeImportFile",
            "payload": {"source_file_reference": selected.file_ref}
        });
        assert_eq!(
            resolve_import_reference(&mut replay, &mut registry)
                .unwrap_err()
                .to_string(),
            "FINANCE_IMPORT_FILE_REFERENCE_INVALID"
        );
    }
}
