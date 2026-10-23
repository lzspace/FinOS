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
    selectLocalModelFile: () => core.invoke("select_local_model_file"),
    selectLocalModelManifest: () => core.invoke("select_local_model_manifest"),
    selectLocalAIRuntime: () => core.invoke("select_local_ai_runtime"),
    invoke: (requestId, operation, payload) => core.invoke("finance_invoke", { requestId, operation, payload }),
    getRuntimeStatus: (requestId) => core.invoke("get_runtime_status", { requestId })
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
async fn select_local_model_file(
    app: tauri::AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<Option<SelectedImportFile>, HostError> {
    select_registered_file(
        app,
        state,
        "Lokales GGUF-Modell auswählen",
        "GGUF-Modell",
        &["gguf"],
        true,
    )
    .await
}

#[tauri::command]
async fn select_local_model_manifest(
    app: tauri::AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<Option<SelectedImportFile>, HostError> {
    select_registered_file(
        app,
        state,
        "Modellmanifest auswählen",
        "JSON-Manifest",
        &["json"],
        false,
    )
    .await
}

#[tauri::command]
async fn select_local_ai_runtime(
    app: tauri::AppHandle,
    state: tauri::State<'_, DesktopState>,
) -> Result<Option<SelectedImportFile>, HostError> {
    let (sender, receiver) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .set_title("llama.cpp Runtime auswählen")
        .pick_file(move |selected| {
            let _ = sender.send(selected);
        });
    let selected = receiver
        .await
        .map_err(|_| HostError::SafeCode("LOCAL_AI_RUNTIME_NOT_FOUND".into()))?;
    let Some(FilePath::Path(path)) = selected else {
        return Ok(None);
    };
    let (canonical, size_bytes) = state
        .path_policy
        .validate_runtime(&path)
        .map_err(|error| HostError::SafeCode(error.to_string()))?;
    let display_name = canonical
        .file_name()
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| HostError::SafeCode("LOCAL_AI_RUNTIME_INCOMPATIBLE".into()))?
        .to_owned();
    Ok(Some(
        state
            .files
            .lock()
            .map_err(|_| HostError::SafeCode("DESKTOP_STATE_UNAVAILABLE".into()))?
            .register_runtime(canonical, display_name, size_bytes),
    ))
}

async fn select_registered_file(
    app: tauri::AppHandle,
    state: tauri::State<'_, DesktopState>,
    title: &str,
    filter_name: &str,
    extensions: &[&str],
    model: bool,
) -> Result<Option<SelectedImportFile>, HostError> {
    let (sender, receiver) = tokio::sync::oneshot::channel();
    app.dialog()
        .file()
        .set_title(title)
        .add_filter(filter_name, extensions)
        .pick_file(move |selected| {
            let _ = sender.send(selected);
        });
    let selected = receiver
        .await
        .map_err(|_| HostError::SafeCode("LOCAL_MODEL_FILE_DIALOG_FAILED".into()))?;
    let Some(FilePath::Path(path)) = selected else {
        return Ok(None);
    };
    let (canonical, size_bytes) = if model {
        state.path_policy.validate_model(&path)
    } else {
        state.path_policy.validate_model_manifest(&path)
    }
    .map_err(|error| HostError::SafeCode(error.to_string()))?;
    let display_name = canonical
        .file_name()
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| HostError::SafeCode("LOCAL_MODEL_FILE_INVALID".into()))?
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
    request_id: String,
    operation: String,
    mut payload: Value,
    state: tauri::State<'_, DesktopState>,
) -> ResponseEnvelope {
    if operation != "query" && operation != "command" {
        return ResponseEnvelope::error(request_id, operation, "DESKTOP_OPERATION_FAILED");
    }

    if operation == "command" {
        let resolved = state
            .files
            .lock()
            .map_err(|_| HostError::SafeCode("DESKTOP_OPERATION_FAILED".into()))
            .and_then(|mut registry| {
                resolve_import_reference(&mut payload, &mut registry)?;
                resolve_model_references(&mut payload, &mut registry)?;
                resolve_runtime_reference(&mut payload, &mut registry)
            });
        if let Err(error) = resolved {
            return ResponseEnvelope::error(request_id, operation, &error.to_string());
        }
    }

    let mut application = match state.application.lock() {
        Ok(application) => application,
        Err(_) => {
            return ResponseEnvelope::error(request_id, operation, "DESKTOP_WORKER_UNAVAILABLE")
        }
    };
    match application.send(&request_id, &operation, payload) {
        Ok(response) => response,
        Err(error) => ResponseEnvelope::error(request_id, operation, &error.to_string()),
    }
}

fn resolve_runtime_reference(
    payload: &mut Value,
    registry: &mut FileRegistry,
) -> Result<(), HostError> {
    if payload.get("name").and_then(Value::as_str) != Some("RegisterLocalAIRuntime") {
        return Ok(());
    }
    let body = payload
        .get_mut("payload")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| HostError::SafeCode("DESKTOP_OPERATION_PAYLOAD_INVALID".into()))?;
    let runtime_ref = body
        .remove("runtime_file_reference")
        .and_then(|value| value.as_str().map(ToOwned::to_owned))
        .ok_or_else(|| HostError::SafeCode("LOCAL_AI_RUNTIME_NOT_FOUND".into()))?;
    let runtime_path = registry
        .take(&runtime_ref)
        .ok_or_else(|| HostError::SafeCode("LOCAL_AI_RUNTIME_NOT_FOUND".into()))?;
    body.remove("runtime_path");
    body.insert(
        "runtime_path".into(),
        Value::String(runtime_path.to_string_lossy().into_owned()),
    );
    Ok(())
}

fn resolve_model_references(
    payload: &mut Value,
    registry: &mut FileRegistry,
) -> Result<(), HostError> {
    if payload.get("name").and_then(Value::as_str) != Some("RegisterLocalModel") {
        return Ok(());
    }
    let body = payload
        .get_mut("payload")
        .and_then(Value::as_object_mut)
        .ok_or_else(|| HostError::SafeCode("DESKTOP_OPERATION_PAYLOAD_INVALID".into()))?;
    let model_ref = body
        .remove("model_file_reference")
        .and_then(|value| value.as_str().map(ToOwned::to_owned))
        .ok_or_else(|| HostError::SafeCode("LOCAL_MODEL_REFERENCE_EXPIRED".into()))?;
    let manifest_ref = body
        .remove("manifest_file_reference")
        .and_then(|value| value.as_str().map(ToOwned::to_owned))
        .ok_or_else(|| HostError::SafeCode("LOCAL_MODEL_REFERENCE_EXPIRED".into()))?;
    let (model_path, manifest_path) = registry
        .take_pair(&model_ref, &manifest_ref)
        .map_err(|_| HostError::SafeCode("LOCAL_MODEL_REFERENCE_EXPIRED".into()))?;
    body.remove("model_path");
    body.remove("manifest_path");
    body.insert(
        "model_path".into(),
        Value::String(model_path.to_string_lossy().into_owned()),
    );
    body.insert(
        "manifest_path".into(),
        Value::String(manifest_path.to_string_lossy().into_owned()),
    );
    Ok(())
}

fn resolve_import_reference(
    payload: &mut Value,
    registry: &mut FileRegistry,
) -> Result<(), HostError> {
    let Some(command) = payload.get("name").and_then(Value::as_str) else {
        return Ok(());
    };
    if command != "AnalyzeImportFile"
        && command != "AnalyzeUnknownImportFormat"
        && command != "ImportTransactions"
    {
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
    request_id: String,
    state: tauri::State<'_, DesktopState>,
) -> ResponseEnvelope {
    let mut application = match state.application.lock() {
        Ok(application) => application,
        Err(_) => {
            return ResponseEnvelope::error(
                request_id,
                "runtime_status",
                "DESKTOP_WORKER_UNAVAILABLE",
            )
        }
    };
    match application.send(&request_id, "runtime_status", json!({})) {
        Ok(response) => response,
        Err(error) => ResponseEnvelope::error(request_id, "runtime_status", &error.to_string()),
    }
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

fn configured_runtime(executable_directory: &Path) -> PathBuf {
    let bundled = bundled_runtime(executable_directory);
    if bundled.is_file() {
        return bundled;
    }
    if cfg!(debug_assertions) {
        if let Some(program) = std::env::var_os("FINANCE_LLAMA_CPP_EXECUTABLE") {
            let configured = PathBuf::from(program);
            if configured.is_absolute() {
                return configured;
            }
        }
    }
    bundled
}

fn bundled_runtime(executable_directory: &Path) -> PathBuf {
    let direct = executable_directory.join("llama-cli");
    if direct.is_file() {
        return direct;
    }
    let resource = executable_directory.join("../Resources/llama.cpp/llama-cli");
    if resource.is_file() {
        return resource;
    }
    executable_directory.join("../Resources/llama-cli")
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
            select_local_model_file,
            select_local_model_manifest,
            select_local_ai_runtime,
            finance_invoke,
            get_runtime_status,
            register_test_fixture
        ]);
    }
    #[cfg(not(feature = "e2e-fixtures"))]
    {
        builder = builder.invoke_handler(tauri::generate_handler![
            select_import_file,
            select_local_model_file,
            select_local_model_manifest,
            select_local_ai_runtime,
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
            let executable_directory = std::env::current_exe()?
                .parent()
                .ok_or("desktop executable directory missing")?
                .to_path_buf();
            let runtime = configured_runtime(&executable_directory);
            worker_args.extend([
                "--data-dir".into(),
                store_root.to_string_lossy().into_owned(),
                "--llama-cpp-executable".into(),
                runtime.to_string_lossy().into_owned(),
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

    #[test]
    fn resolves_unknown_format_reference_inside_host() {
        let mut registry = FileRegistry::default();
        let selected = registry.register(
            PathBuf::from("/private/tmp/unknown.csv"),
            "unknown.csv".into(),
            24,
        );
        let mut payload = json!({
            "name": "AnalyzeUnknownImportFormat",
            "payload": {
                "source_file_reference": selected.file_ref,
                "bank_identifier": "example-bank"
            }
        });

        resolve_import_reference(&mut payload, &mut registry).unwrap();

        assert_eq!(
            payload["payload"]["source_file_path"],
            Value::String("/private/tmp/unknown.csv".into())
        );
        assert!(payload["payload"].get("source_file_reference").is_none());
    }

    #[test]
    fn model_references_remain_valid_together_and_expire_with_stable_code() {
        let mut registry = FileRegistry::default();
        let model = registry.register(
            PathBuf::from("/private/tmp/model.gguf"),
            "model.gguf".into(),
            12,
        );
        let manifest = registry.register(
            PathBuf::from("/private/tmp/model.json"),
            "model.json".into(),
            12,
        );
        let mut payload = json!({"name": "RegisterLocalModel", "payload": {
            "model_file_reference": model.file_ref,
            "manifest_file_reference": manifest.file_ref,
        }});
        resolve_model_references(&mut payload, &mut registry).unwrap();
        assert_eq!(payload["payload"]["model_path"], "/private/tmp/model.gguf");
        assert_eq!(
            payload["payload"]["manifest_path"],
            "/private/tmp/model.json"
        );

        let mut replay = json!({"name": "RegisterLocalModel", "payload": {
            "model_file_reference": model.file_ref,
            "manifest_file_reference": manifest.file_ref,
        }});
        assert_eq!(
            resolve_model_references(&mut replay, &mut registry)
                .unwrap_err()
                .to_string(),
            "LOCAL_MODEL_REFERENCE_EXPIRED"
        );
    }

    #[test]
    fn bundled_runtime_resolution_never_uses_shell_path() {
        let executable_directory = Path::new("/Applications/Agent OS Finance.app/Contents/MacOS");
        assert_eq!(
            bundled_runtime(executable_directory),
            PathBuf::from(
                "/Applications/Agent OS Finance.app/Contents/MacOS/../Resources/llama-cli"
            )
        );
    }

    #[test]
    fn runtime_reference_is_resolved_only_for_registration() {
        let mut registry = FileRegistry::default();
        let selected = registry.register_runtime(
            PathBuf::from("/private/tmp/llama-cli"),
            "llama-cli".into(),
            12,
        );
        let mut payload = json!({"name": "RegisterLocalAIRuntime", "payload": {
            "runtime_file_reference": selected.file_ref,
            "runtime_path": "/untrusted/frontend/path"
        }});
        resolve_runtime_reference(&mut payload, &mut registry).unwrap();
        assert_eq!(payload["payload"]["runtime_path"], "/private/tmp/llama-cli");
        assert!(payload["payload"].get("runtime_file_reference").is_none());

        let mut replay = json!({"name": "RegisterLocalAIRuntime", "payload": {
            "runtime_file_reference": selected.file_ref
        }});
        assert_eq!(
            resolve_runtime_reference(&mut replay, &mut registry)
                .unwrap_err()
                .to_string(),
            "LOCAL_AI_RUNTIME_NOT_FOUND"
        );
    }

    #[test]
    fn existing_bundled_runtime_is_preferred() {
        let root = tempfile::tempdir().unwrap();
        let bundled = root.path().join("llama-cli");
        std::fs::write(&bundled, b"bundled").unwrap();
        assert_eq!(configured_runtime(root.path()), bundled);
    }
}
