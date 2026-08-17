fn main() {
    let protoc = protoc_bin_vendored::protoc_bin_path().expect("vendored protoc is available");
    std::env::set_var("PROTOC", protoc);
    tonic_prost_build::configure()
        .build_client(true)
        .build_server(true)
        .compile_protos(
            &["../../../contracts/proto/media_query.proto"],
            &["../../../contracts/proto"],
        )
        .expect("media_query.proto compiles for Rust");
    println!("cargo:rerun-if-changed=../../../contracts/proto/media_query.proto");
    tauri_build::build();
}
