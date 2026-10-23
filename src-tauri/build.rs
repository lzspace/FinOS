fn main() {
    if std::env::var("PROFILE").as_deref() == Ok("release") {
        let runtime = std::path::Path::new("resources/llama.cpp/llama-cli");
        if !runtime.is_file() {
            panic!("release requires a staged, self-contained llama.cpp runtime");
        }
    }
    tauri_build::build()
}
