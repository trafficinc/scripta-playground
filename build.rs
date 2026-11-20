use std::{env, fs, path::PathBuf, process::Command};

fn main() {
    println!("cargo:rerun-if-env-changed=SCR_EMBED_INTERPRETER");
    println!("cargo:rerun-if-env-changed=SCR_EMBED_NOTICES");
    if env::var_os("CARGO_FEATURE_BUNDLED_INTERPRETER").is_none() {
        return;
    }
    let binary = PathBuf::from(env::var_os("SCR_EMBED_INTERPRETER").expect(
        "bundled-interpreter requires SCR_EMBED_INTERPRETER; run python3 scripts/build-standalone.py",
    ));
    let notices = PathBuf::from(env::var_os("SCR_EMBED_NOTICES").expect(
        "bundled-interpreter requires SCR_EMBED_NOTICES; run python3 scripts/build-standalone.py",
    ));
    assert_eq!(
        env::var("HOST").unwrap(),
        env::var("TARGET").unwrap(),
        "build the standalone executable natively on its target platform"
    );
    let output = Command::new(&binary)
        .args(["version", "--verbose"])
        .output()
        .expect("cannot run interpreter to verify its identity");
    assert!(output.status.success(), "interpreter version check failed");
    let version = String::from_utf8(output.stdout).expect("invalid interpreter version output");
    let first = version.lines().next().expect("missing interpreter version");
    let number = first
        .strip_prefix("scripta ")
        .or_else(|| first.strip_prefix("scr "))
        .expect("embedded executable must identify itself as Scripta");
    assert!(
        number
            .split('.')
            .take(2)
            .all(|part| part.parse::<u64>().is_ok())
            && number.split('.').count() >= 3,
        "invalid interpreter version"
    );
    let target = format!("target: {}", env::var("TARGET").unwrap());
    assert!(
        version.lines().any(|line| line == target),
        "interpreter target does not match playground target"
    );
    println!("cargo:rerun-if-changed={}", binary.display());
    println!("cargo:rerun-if-changed={}", notices.display());
    println!("cargo:rustc-env=SCR_BUNDLED_VERSION={number}");
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    fs::copy(binary, out.join("scripta.bin")).expect("cannot copy embedded interpreter");
    fs::copy(notices, out.join("licenses.txt")).expect("cannot copy embedded licenses");
}
