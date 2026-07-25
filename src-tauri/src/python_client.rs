use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

use serde_json::Value;
use thiserror::Error;

use crate::protocol::{RequestEnvelope, ResponseEnvelope};

const MAX_RESPONSE_BYTES: usize = 8 * 1024 * 1024;

#[derive(Debug, Error)]
pub enum PythonClientError {
    #[error("DESKTOP_APPLICATION_PROCESS_START_FAILED")]
    Start,
    #[error("DESKTOP_APPLICATION_PROCESS_TERMINATED")]
    Terminated,
    #[error("DESKTOP_RESPONSE_INVALID")]
    InvalidResponse,
    #[error("DESKTOP_CONTRACT_INCOMPATIBLE")]
    IncompatibleContract,
}

pub struct PythonClient {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
}

impl PythonClient {
    pub fn spawn(program: &Path, arguments: &[String]) -> Result<Self, PythonClientError> {
        let mut child = Command::new(program)
            .args(arguments)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .env_remove("PYTHONINSPECT")
            .env("PYTHONUNBUFFERED", "1")
            .spawn()
            .map_err(|_| PythonClientError::Start)?;
        let input = child.stdin.take().ok_or(PythonClientError::Start)?;
        let output = child.stdout.take().ok_or(PythonClientError::Start)?;
        Ok(Self {
            child,
            input,
            output: BufReader::new(output),
        })
    }

    pub fn send(
        &mut self,
        operation: &str,
        payload: Value,
    ) -> Result<ResponseEnvelope, PythonClientError> {
        if self
            .child
            .try_wait()
            .map_err(|_| PythonClientError::Terminated)?
            .is_some()
        {
            return Err(PythonClientError::Terminated);
        }
        let request = RequestEnvelope::new(operation, payload);
        serde_json::to_writer(&mut self.input, &request)
            .map_err(|_| PythonClientError::Terminated)?;
        self.input
            .write_all(b"\n")
            .and_then(|_| self.input.flush())
            .map_err(|_| PythonClientError::Terminated)?;

        let mut line = String::new();
        let bytes = self
            .output
            .read_line(&mut line)
            .map_err(|_| PythonClientError::Terminated)?;
        if bytes == 0 {
            return Err(PythonClientError::Terminated);
        }
        if bytes > MAX_RESPONSE_BYTES {
            return Err(PythonClientError::InvalidResponse);
        }
        let response: ResponseEnvelope =
            serde_json::from_str(&line).map_err(|_| PythonClientError::InvalidResponse)?;
        response.validate_for(&request).map_err(|code| match code {
            "DESKTOP_CONTRACT_INCOMPATIBLE" => PythonClientError::IncompatibleContract,
            _ => PythonClientError::InvalidResponse,
        })?;
        Ok(response)
    }
}

impl Drop for PythonClient {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    fn script(body: &str) -> (tempfile::TempDir, std::path::PathBuf) {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("worker");
        fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        (root, path)
    }

    #[test]
    fn invokes_application_process_with_structured_envelope() {
        let (_root, path) = script(
            r#"read line
id=$(printf '%s' "$line" | sed -n 's/.*"request_id":"\([^"]*\)".*/\1/p')
printf '{"request_id":"%s","operation":"query","contract_version":"1.3.0","status":"OK","result":{"schema_version":"1.0.0"}}\n' "$id""#,
        );
        let mut client = PythonClient::spawn(&path, &[]).unwrap();
        let response = client.send("query", serde_json::json!({})).unwrap();
        assert_eq!(response.status, "OK");
    }

    #[test]
    fn detects_process_abort_and_invalid_response() {
        let (_root, path) = script("exit 7");
        let mut client = PythonClient::spawn(&path, &[]).unwrap();
        assert!(matches!(
            client.send("query", serde_json::json!({})),
            Err(PythonClientError::Terminated)
        ));

        let (_root, path) = script("read line\nprintf 'not-json\\n'");
        let mut client = PythonClient::spawn(&path, &[]).unwrap();
        assert!(matches!(
            client.send("query", serde_json::json!({})),
            Err(PythonClientError::InvalidResponse)
        ));
    }
}
