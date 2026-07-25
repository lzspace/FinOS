use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const CONTRACT_VERSION: &str = "1.3.0";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RequestEnvelope {
    pub request_id: String,
    pub operation: String,
    pub contract_version: String,
    pub payload: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProtocolError {
    pub code: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ResponseEnvelope {
    pub request_id: String,
    pub operation: String,
    pub contract_version: String,
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ProtocolError>,
}

impl RequestEnvelope {
    pub fn new(operation: impl Into<String>, payload: Value) -> Self {
        Self {
            request_id: format!("req_{}", uuid::Uuid::new_v4().simple()),
            operation: operation.into(),
            contract_version: CONTRACT_VERSION.to_owned(),
            payload,
        }
    }
}

impl ResponseEnvelope {
    pub fn validate_for(&self, request: &RequestEnvelope) -> Result<(), &'static str> {
        if self.request_id != request.request_id || self.operation != request.operation {
            return Err("DESKTOP_RESPONSE_INVALID");
        }
        if self.contract_version != CONTRACT_VERSION {
            return Err("DESKTOP_CONTRACT_INCOMPATIBLE");
        }
        match self.status.as_str() {
            "OK" if self.result.is_some() && self.error.is_none() => Ok(()),
            "ERROR" if self.error.is_some() && self.result.is_none() => Ok(()),
            _ => Err("DESKTOP_RESPONSE_INVALID"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rejects_contract_incompatibility() {
        let request = RequestEnvelope::new("query", json!({}));
        let response = ResponseEnvelope {
            request_id: request.request_id.clone(),
            operation: "query".into(),
            contract_version: "9.0.0".into(),
            status: "OK".into(),
            result: Some(json!({})),
            error: None,
        };
        assert_eq!(
            response.validate_for(&request),
            Err("DESKTOP_CONTRACT_INCOMPATIBLE")
        );
    }

    #[test]
    fn rejects_mismatched_or_ambiguous_response() {
        let request = RequestEnvelope::new("query", json!({}));
        let response = ResponseEnvelope {
            request_id: "different".into(),
            operation: "query".into(),
            contract_version: CONTRACT_VERSION.into(),
            status: "OK".into(),
            result: Some(json!({})),
            error: None,
        };
        assert_eq!(
            response.validate_for(&request),
            Err("DESKTOP_RESPONSE_INVALID")
        );
    }
}
