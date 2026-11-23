use process_wrap::std::{StdChildWrapper, StdCommandWrap};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    env, fs,
    io::{self, BufReader, Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

const MAX_SOURCE: usize = 256 * 1024;
const MAX_OUTPUT: u64 = 1024 * 1024;
type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

struct State {
    interpreter: PathBuf,
    host: String,
    token: String,
    running: Mutex<()>,
    cancel: AtomicBool,
    shutdown: AtomicBool,
    connections: AtomicUsize,
}

fn main() {
    if let Err(error) = start() {
        eprintln!("Scripta Playground: {error}");
        std::process::exit(1);
    }
}

struct Interpreter {
    path: PathBuf,
    // Keep the extracted binary alive through server and child-process shutdown.
    _directory: Option<tempfile::TempDir>,
}

fn version_number(output: &[u8]) -> Option<&str> {
    let line = std::str::from_utf8(output).ok()?.lines().next()?;
    let number = line
        .strip_prefix("scripta ")
        .or_else(|| line.strip_prefix("scr "))?;
    let mut parts = number.split('.');
    parts.next()?.parse::<u64>().ok()?;
    parts.next()?.parse::<u64>().ok()?;
    parts.next()?;
    Some(number)
}

fn validate_interpreter(path: &Path) -> Result<()> {
    let output = Command::new(path)
        .arg("version")
        .stdin(Stdio::null())
        .output()?;
    let version = version_number(&output.stdout)
        .filter(|_| output.status.success())
        .ok_or("selected executable did not identify itself as Scripta")?;
    #[cfg(feature = "bundled-interpreter")]
    if version
        .split('.')
        .take(2)
        .ne(env!("SCR_BUNDLED_VERSION").split('.').take(2))
    {
        return Err(format!(
            "Scripta {version} is incompatible with bundled {} (major/minor must match)",
            env!("SCR_BUNDLED_VERSION")
        )
        .into());
    }
    let _ = version;
    Ok(())
}

fn select_interpreter(
    explicit: Option<PathBuf>,
    candidates: impl IntoIterator<Item = PathBuf>,
) -> Result<Interpreter> {
    if let Some(path) = explicit {
        let path = fs::canonicalize(path)?;
        validate_interpreter(&path)?;
        return Ok(Interpreter {
            path,
            _directory: None,
        });
    }
    for candidate in candidates {
        if candidate.is_file() && validate_interpreter(&candidate).is_ok() {
            return Ok(Interpreter {
                path: fs::canonicalize(candidate)?,
                _directory: None,
            });
        }
    }
    #[cfg(feature = "bundled-interpreter")]
    {
        let directory = tempfile::Builder::new()
            .prefix("scripta-playground-")
            .tempdir()?;
        let path = directory
            .path()
            .join(format!("scripta{}", env::consts::EXE_SUFFIX));
        fs::write(
            &path,
            include_bytes!(concat!(env!("OUT_DIR"), "/scripta.bin")),
        )?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o700))?;
        }
        validate_interpreter(&path)?;
        Ok(Interpreter {
            path,
            _directory: Some(directory),
        })
    }
    #[cfg(not(feature = "bundled-interpreter"))]
    Err("Scripta interpreter not found. Use a standalone build, the complete playground download, or pass --scr /path/to/scripta.".into())
}

fn interpreter_path(explicit: Option<PathBuf>) -> Result<Interpreter> {
    let names = ["scripta", "scr"].map(|name| format!("{name}{}", env::consts::EXE_SUFFIX));
    let executable = env::current_exe()?;
    let directory = executable
        .parent()
        .ok_or("cannot locate application directory")?;
    let mut candidates: Vec<PathBuf> = env::var_os("SCR_BIN")
        .map(PathBuf::from)
        .into_iter()
        .collect();
    let mut directories = vec![directory.join("runtime"), directory.to_path_buf()];
    if let Some(path) = env::var_os("PATH") {
        directories.extend(env::split_paths(&path));
    }
    for dir in directories {
        candidates.extend(names.iter().map(|name| dir.join(name)));
    }
    select_interpreter(explicit, candidates)
}

fn start() -> Result<()> {
    let mut args = env::args_os().skip(1);
    let mut explicit = None;
    let mut open = true;
    while let Some(arg) = args.next() {
        if arg == "--scr" {
            explicit = Some(PathBuf::from(
                args.next().ok_or("--scr needs an executable path")?,
            ));
        } else if arg == "--no-open" {
            open = false;
        } else if arg == "--version" || arg == "-V" {
            println!("Scripta Playground {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        } else if arg == "--help" || arg == "-h" {
            println!(
                "Scripta Playground\nUsage: scr-playground [--scr /path/to/scripta] [--no-open] [--licenses] [--version]\nRuns locally at a random loopback port. Only run scripts you trust.\nClose with the Quit button or Ctrl+C."
            );
            return Ok(());
        } else if arg == "--licenses" {
            #[cfg(feature = "bundled-interpreter")]
            print!(
                "{}",
                include_str!(concat!(env!("OUT_DIR"), "/licenses.txt"))
            );
            #[cfg(not(feature = "bundled-interpreter"))]
            print!("{}", include_str!("../LICENSE"));
            return Ok(());
        } else {
            return Err(format!("unknown argument: {}", arg.to_string_lossy()).into());
        }
    }
    let interpreter = interpreter_path(explicit)?;
    println!(
        "Interpreter: {} ({})",
        interpreter.path.display(),
        if interpreter._directory.is_some() {
            "bundled"
        } else {
            "installed"
        }
    );
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    listener.set_nonblocking(true)?;
    let host = listener.local_addr()?.to_string();
    let mut random = [0u8; 32];
    getrandom::fill(&mut random).map_err(|e| format!("cannot generate session token: {e}"))?;
    let token: String = random.iter().map(|byte| format!("{byte:02x}")).collect();
    let state = Arc::new(State {
        interpreter: interpreter.path.clone(),
        host,
        token,
        running: Mutex::new(()),
        cancel: AtomicBool::new(false),
        shutdown: AtomicBool::new(false),
        connections: AtomicUsize::new(0),
    });
    let signal_state = state.clone();
    ctrlc::set_handler(move || {
        signal_state.shutdown.store(true, Ordering::SeqCst);
        signal_state.cancel.store(true, Ordering::SeqCst);
    })?;
    let url = format!("http://{}/#{}", state.host, state.token);
    println!(
        "Scripta Playground\n{url}\nRuns locally. Only run scripts you trust. Quit in the page or press Ctrl+C."
    );
    io::stdout().flush()?;
    if open && !open_browser(&url) {
        eprintln!("Open the URL above in your browser.");
    }
    while !state.shutdown.load(Ordering::SeqCst) {
        match listener.accept() {
            Ok((stream, _)) => {
                if state.connections.load(Ordering::SeqCst) >= 16 {
                    continue;
                }
                state.connections.fetch_add(1, Ordering::SeqCst);
                let state = state.clone();
                thread::spawn(move || {
                    if let Err(error) = handle(stream, &state) {
                        eprintln!("Request: {error}");
                    }
                    state.connections.fetch_sub(1, Ordering::SeqCst);
                });
            }
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                thread::sleep(Duration::from_millis(10))
            }
            Err(error) => return Err(error.into()),
        }
    }
    state.cancel.store(true, Ordering::SeqCst);
    // Let active execution clean up its process group and temporary directory.
    let _guard = state
        .running
        .lock()
        .map_err(|_| "execution lock poisoned")?;
    Ok(())
}

fn open_browser(url: &str) -> bool {
    #[cfg(target_os = "macos")]
    let mut command = Command::new("open");
    #[cfg(target_os = "windows")]
    let mut command = {
        let mut c = Command::new("rundll32.exe");
        c.arg("url.dll,FileProtocolHandler");
        c
    };
    #[cfg(not(any(target_os = "macos", target_os = "windows")))]
    let mut command = Command::new("xdg-open");
    command
        .arg(url)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success())
}

struct Request {
    method: String,
    path: String,
    headers: BTreeMap<String, String>,
    body: Vec<u8>,
}

fn read_request(stream: &mut impl Read) -> Result<Request> {
    let mut reader = BufReader::new(stream);
    let mut head = Vec::new();
    while !head.ends_with(b"\r\n\r\n") {
        if head.len() >= 16 * 1024 {
            return Err("request headers too large".into());
        }
        let mut byte = [0];
        reader.read_exact(&mut byte)?;
        head.push(byte[0]);
    }
    let mut parsed_headers = [httparse::EMPTY_HEADER; 128];
    let mut parsed = httparse::Request::new(&mut parsed_headers);
    if !parsed.parse(&head)?.is_complete() {
        return Err("incomplete request headers".into());
    }
    let mut headers: BTreeMap<String, String> = BTreeMap::new();
    for header in parsed.headers.iter() {
        let name = header.name.to_ascii_lowercase();
        let value = String::from_utf8_lossy(header.value).trim().to_owned();
        if let Some(previous) = headers.get_mut(&name) {
            // Authentication and framing must remain unambiguous. Ordinary
            // list-valued browser/extension headers may legitimately repeat.
            if matches!(
                name.as_str(),
                "host"
                    | "content-length"
                    | "transfer-encoding"
                    | "origin"
                    | "x-playground-token"
                    | "content-type"
            ) {
                return Err("duplicate security or framing header".into());
            }
            previous.push_str(if name == "cookie" { "; " } else { ", " });
            previous.push_str(&value);
        } else {
            headers.insert(name, value);
        }
    }
    if headers.contains_key("transfer-encoding") {
        return Err("chunked requests are unsupported".into());
    }
    let size = headers
        .get("content-length")
        .map(|s| s.parse::<usize>())
        .transpose()?
        .unwrap_or(0);
    if size > MAX_SOURCE + 4096 {
        return Err("request body too large".into());
    }
    let mut body = vec![0; size];
    reader.read_exact(&mut body)?;
    Ok(Request {
        method: parsed.method.ok_or("missing request method")?.into(),
        path: parsed.path.ok_or("missing request path")?.into(),
        headers,
        body,
    })
}

fn respond(
    stream: &mut impl Write,
    status: &str,
    content_type: &str,
    body: &[u8],
) -> io::Result<()> {
    write!(
        stream,
        "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nReferrer-Policy: no-referrer\r\nContent-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'\r\n\r\n",
        body.len()
    )?;
    stream.write_all(body)
}

fn valid_session(request: &Request, state: &State) -> bool {
    request.method == "POST"
        && request.headers.get("host") == Some(&state.host)
        && request.headers.get("origin") == Some(&format!("http://{}", state.host))
        && request.headers.get("x-playground-token") == Some(&state.token)
        && request.headers.get("content-type").map(String::as_str) == Some("application/json")
}

fn handle(mut stream: TcpStream, state: &State) -> Result<()> {
    // The listener is nonblocking for shutdown polling. Accepted sockets can
    // inherit that mode on macOS; HTTP reads/writes here are intentionally
    // blocking and use their own deadlines instead.
    stream.set_nonblocking(false)?;
    // Browsers may establish speculative connections before sending a request.
    stream.set_read_timeout(Some(Duration::from_secs(15)))?;
    stream.set_write_timeout(Some(Duration::from_secs(5)))?;
    serve(&mut stream, state)
}

fn serve(mut stream: &mut (impl Read + Write), state: &State) -> Result<()> {
    let request = match read_request(&mut stream) {
        Ok(request) => request,
        Err(error) => {
            if error.downcast_ref::<io::Error>().is_some_and(|error| {
                matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock
                        | io::ErrorKind::TimedOut
                        | io::ErrorKind::UnexpectedEof
                        | io::ErrorKind::ConnectionReset
                        | io::ErrorKind::ConnectionAborted
                )
            }) {
                // An idle/abandoned transport is not malformed HTTP. In
                // particular, don't send 400 on a browser's preconnect socket.
                return Ok(());
            }
            eprintln!("Rejected HTTP request: {error}");
            respond(
                &mut stream,
                "400 Bad Request",
                "text/plain",
                format!("Invalid HTTP request: {error}").as_bytes(),
            )?;
            return Ok(());
        }
    };
    if request.headers.get("host") != Some(&state.host) {
        respond(&mut stream, "403 Forbidden", "text/plain", b"Invalid host")?;
        return Ok(());
    }
    if request.method == "GET" {
        let asset: Option<(&str, &[u8])> = match request.path.as_str() {
            "/" => Some((
                "text/html; charset=utf-8",
                include_bytes!("../web/index.html"),
            )),
            "/app.js" => Some((
                "text/javascript; charset=utf-8",
                include_bytes!("../web/app.js"),
            )),
            "/style.css" => Some((
                "text/css; charset=utf-8",
                include_bytes!("../web/style.css"),
            )),
            _ => None,
        };
        if let Some((kind, body)) = asset {
            respond(&mut stream, "200 OK", kind, body)?;
        } else {
            respond(&mut stream, "404 Not Found", "text/plain", b"Not found")?;
        }
        return Ok(());
    }
    if !valid_session(&request, state) {
        respond(
            &mut stream,
            "403 Forbidden",
            "text/plain",
            b"Invalid session",
        )?;
        return Ok(());
    }
    let (status, result) = match request.path.as_str() {
        "/run" => match state.running.try_lock() {
            Ok(_guard) => {
                // Reset before checking shutdown so shutdown cannot be lost.
                state.cancel.store(false, Ordering::SeqCst);
                if state.shutdown.load(Ordering::SeqCst) {
                    return Ok(());
                }
                let result = execute(&request.body, state)
                    .unwrap_or_else(|e| json!({"error":e.to_string()}));
                ("200 OK", result)
            }
            Err(_) => (
                "409 Conflict",
                json!({"error":"A script is already running."}),
            ),
        },
        "/stop" => {
            state.cancel.store(true, Ordering::SeqCst);
            ("200 OK", json!({"ok":true}))
        }
        "/shutdown" => {
            state.cancel.store(true, Ordering::SeqCst);
            // Respond before asking the server loop to exit.
            respond(&mut stream, "200 OK", "application/json", b"{\"ok\":true}")?;
            state.shutdown.store(true, Ordering::SeqCst);
            return Ok(());
        }
        _ => ("404 Not Found", json!({"error":"Not found"})),
    };
    respond(
        &mut stream,
        status,
        "application/json",
        &serde_json::to_vec(&result)?,
    )?;
    Ok(())
}

struct ChildGuard(Box<dyn StdChildWrapper>);
impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn execute(body: &[u8], state: &State) -> Result<Value> {
    let request: Value = serde_json::from_slice(body)?;
    let source = request["source"]
        .as_str()
        .ok_or("source must be a string")?;
    if source.len() > MAX_SOURCE {
        return Err("source exceeds 256 KiB".into());
    }
    let directory = tempfile::tempdir()?;
    fs::write(directory.path().join("playground.scripta"), source)?;
    let stdout_path = directory.path().join("stdout");
    let stderr_path = directory.path().join("stderr");
    let mut command = Command::new(&state.interpreter);
    command
        .args(["run", "playground.scripta"])
        .current_dir(directory.path())
        .stdin(Stdio::null())
        .stdout(fs::File::create(&stdout_path)?)
        .stderr(fs::File::create(&stderr_path)?)
        .env("SCR_MAX_STEPS", "10000000")
        .env("SCR_MAX_VALUE_BYTES", "8388608")
        .env("SCR_MAX_SOURCE_BYTES", MAX_SOURCE.to_string())
        .env("SCR_MAX_SCOPES", "10000");
    let mut wrapped = StdCommandWrap::from(command);
    #[cfg(unix)]
    wrapped.wrap(process_wrap::std::ProcessGroup::leader());
    #[cfg(windows)]
    wrapped.wrap(process_wrap::std::JobObject);
    let started = Instant::now();
    let mut child = ChildGuard(wrapped.spawn()?);
    let (code, reason) = loop {
        if state.cancel.load(Ordering::SeqCst) {
            break (None, Some("Stopped"));
        }
        if started.elapsed() > Duration::from_secs(10) {
            break (None, Some("Execution exceeded 10 seconds"));
        }
        if fs::metadata(&stdout_path)?.len() + fs::metadata(&stderr_path)?.len() > MAX_OUTPUT {
            break (None, Some("Output exceeded 1 MiB"));
        }
        if let Some(status) = child.0.try_wait()? {
            break (status.code(), None);
        }
        thread::sleep(Duration::from_millis(10));
    };
    drop(child);
    let capture = |path: PathBuf| -> io::Result<String> {
        let mut bytes = Vec::new();
        fs::File::open(path)?
            .take(MAX_OUTPUT)
            .read_to_end(&mut bytes)?;
        Ok(String::from_utf8_lossy(&bytes).into_owned())
    };
    Ok(
        json!({"stdout":capture(stdout_path)?, "stderr":capture(stderr_path)?,
        "exitCode":code, "reason":reason, "elapsedMs":started.elapsed().as_millis()}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn rejects_ambiguous_or_oversized_http_requests() {
        for request in [
            "POST /run HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 1\r\n\r\n",
            "POST /run HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n",
            "POST /run HTTP/1.1\r\nContent-Length: 999999999\r\n\r\n",
            "GET / HTTP/1.1\r\nHost: a\r\nhost: b\r\n\r\n",
        ] {
            assert!(read_request(&mut Cursor::new(request)).is_err());
        }
        let request = format!("GET / HTTP/1.1\r\nX-Long: {}", "x".repeat(17 * 1024));
        assert!(read_request(&mut Cursor::new(request)).is_err());
    }

    #[test]
    fn parses_bounded_json_request() {
        let request = read_request(&mut Cursor::new(
            "POST /run HTTP/1.1\r\nHost: 127.0.0.1:1234\r\nContent-Length: 2\r\n\r\n{}",
        ))
        .unwrap();
        assert_eq!(request.body, b"{}");
        assert_eq!(request.headers["host"], "127.0.0.1:1234");
    }

    struct BrowserConnection {
        request: Cursor<Vec<u8>>,
        response: Vec<u8>,
    }
    impl Read for BrowserConnection {
        fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
            self.request.read(bytes)
        }
    }
    impl Write for BrowserConnection {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            self.response.extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    fn stylesheet_accepts_browser_headers_and_repeated_noncritical_headers() {
        for version in ["HTTP/1.1", "HTTP/1.0"] {
            let mut bytes = format!("GET /style.css {version}\r\nHost: 127.0.0.1:1234\r\nUser-Agent: Browser\r\nAccept: text/css\r\nAccept: */*;q=0.1\r\nCache-Control: no-cache\r\nCache-Control: max-age=0\r\nSec-Fetch-Dest: style\r\nSec-Fetch-Mode: no-cors\r\nCookie: one=1\r\nCookie: two=2\r\nX-Extension: ").into_bytes();
            // Field values can contain opaque non-UTF-8 bytes (obs-text).
            bytes.extend_from_slice(b"\xe9\r\n\r\n");
            let request = read_request(&mut Cursor::new(&bytes)).unwrap();
            assert_eq!(request.headers["accept"], "text/css, */*;q=0.1");
            assert_eq!(request.headers["cookie"], "one=1; two=2");
            let mut connection = BrowserConnection {
                request: Cursor::new(bytes),
                response: Vec::new(),
            };
            serve(&mut connection, &test_state()).unwrap();
            assert!(
                connection
                    .response
                    .starts_with(b"HTTP/1.1 200 OK\r\nContent-Type: text/css; charset=utf-8\r\n")
            );
            assert!(
                connection
                    .response
                    .ends_with(include_bytes!("../web/style.css"))
            );
        }
    }

    #[test]
    fn ambiguous_authentication_still_returns_400_with_diagnostic() {
        let bytes = b"POST /run HTTP/1.1\r\nHost: 127.0.0.1:1234\r\nOrigin: http://127.0.0.1:1234\r\nOrigin: https://unrelated.example\r\nContent-Length: 0\r\n\r\n";
        let mut connection = BrowserConnection {
            request: Cursor::new(bytes.to_vec()),
            response: Vec::new(),
        };
        serve(&mut connection, &test_state()).unwrap();
        let response = String::from_utf8(connection.response).unwrap();
        assert!(response.starts_with("HTTP/1.1 400 Bad Request"));
        assert!(response.ends_with("Invalid HTTP request: duplicate security or framing header"));
    }

    #[test]
    fn idle_and_abandoned_connections_do_not_receive_bad_request_responses() {
        struct TransportFailure {
            kind: io::ErrorKind,
            response: Vec<u8>,
        }
        impl Read for TransportFailure {
            fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
                Err(io::Error::from(self.kind))
            }
        }
        impl Write for TransportFailure {
            fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
                self.response.extend_from_slice(bytes);
                Ok(bytes.len())
            }
            fn flush(&mut self) -> io::Result<()> {
                Ok(())
            }
        }
        for kind in [
            io::ErrorKind::WouldBlock,
            io::ErrorKind::TimedOut,
            io::ErrorKind::UnexpectedEof,
            io::ErrorKind::ConnectionReset,
            io::ErrorKind::ConnectionAborted,
        ] {
            let mut connection = TransportFailure {
                kind,
                response: Vec::new(),
            };
            serve(&mut connection, &test_state()).unwrap();
            assert!(
                connection.response.is_empty(),
                "transport error {kind:?} produced an HTTP response"
            );
        }
        let mut unused_preconnect = BrowserConnection {
            request: Cursor::new(Vec::new()),
            response: Vec::new(),
        };
        serve(&mut unused_preconnect, &test_state()).unwrap();
        assert!(unused_preconnect.response.is_empty());
    }

    fn test_state() -> State {
        State {
            interpreter: PathBuf::from(
                env::var_os("SCR_TEST_BIN")
                    .expect("Set SCR_TEST_BIN to the built scr executable for execution tests"),
            ),
            host: "127.0.0.1:1234".into(),
            token: "test".into(),
            running: Mutex::new(()),
            cancel: AtomicBool::new(false),
            shutdown: AtomicBool::new(false),
            connections: AtomicUsize::new(0),
        }
    }

    #[test]
    fn installed_interpreter_is_preferred_and_invalid_override_is_an_error() {
        let path = PathBuf::from(env::var_os("SCR_TEST_BIN").expect("Set SCR_TEST_BIN"));
        let selected = select_interpreter(None, [path.clone()]).unwrap();
        assert_eq!(selected.path, fs::canonicalize(path).unwrap());
        assert!(selected._directory.is_none());
        let directory = tempfile::tempdir().unwrap();
        assert!(select_interpreter(Some(directory.path().join("missing")), []).is_err());
    }

    #[cfg(feature = "bundled-interpreter")]
    #[test]
    fn bundled_interpreter_executes_and_is_removed_after_use() {
        let directory = tempfile::tempdir().unwrap();
        let invalid = directory.path().join("invalid-scripta");
        fs::write(&invalid, b"not an executable").unwrap();
        let selected = select_interpreter(None, [invalid]).unwrap();
        assert!(selected._directory.is_some());
        let path = selected.path.clone();
        let source = directory.path().join("test.scripta");
        fs::write(&source, "echo \"42\";").unwrap();
        let output = Command::new(&path).arg("run").arg(source).output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert_eq!(output.stdout, b"42");
        drop(selected);
        assert!(!path.exists(), "extracted interpreter must be cleaned up");
    }

    #[cfg(all(unix, feature = "bundled-interpreter"))]
    #[test]
    fn incompatible_installed_interpreter_falls_back_to_bundle() {
        use std::os::unix::fs::PermissionsExt;
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("scripta");
        fs::write(&path, "#!/bin/sh\necho 'scripta 99999.0.0'\n").unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o700)).unwrap();
        let selected = select_interpreter(None, [path]).unwrap();
        assert!(selected._directory.is_some());
    }
    fn run(source: &str, state: &State) -> Value {
        execute(
            &serde_json::to_vec(&json!({"source":source})).unwrap(),
            state,
        )
        .unwrap()
    }

    #[test]
    fn session_requires_matching_host_origin_token_and_json() {
        let state = test_state();
        let text = "POST /run HTTP/1.1\r\nHost: 127.0.0.1:1234\r\nOrigin: http://127.0.0.1:1234\r\nX-Playground-Token: test\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}";
        let mut request = read_request(&mut Cursor::new(text)).unwrap();
        assert!(valid_session(&request, &state));
        for key in ["host", "origin", "x-playground-token", "content-type"] {
            let original = request.headers.insert(key.into(), "wrong".into()).unwrap();
            assert!(!valid_session(&request, &state));
            request.headers.insert(key.into(), original);
        }
        request.method = "GET".into();
        assert!(!valid_session(&request, &state));
    }

    #[test]
    fn interpreter_returns_output_errors_and_exit_codes() {
        let state = test_state();
        let result = run("echo \"hello\\n\"; exit(7);", &state);
        assert_eq!(result["stdout"], "hello\n");
        assert_eq!(result["exitCode"], 7);
        let result = run("echo ;", &state);
        assert_eq!(result["exitCode"], 1);
        assert!(
            result["stderr"]
                .as_str()
                .unwrap()
                .contains("playground.scripta")
        );
        let result = run(
            "File::write(\"local.txt\",\"ok\"); echo File::read(\"local.txt\");",
            &state,
        );
        assert_eq!(result["stdout"], "ok");
        assert!(!PathBuf::from("local.txt").exists());
    }

    #[test]
    fn cancellation_and_output_limit_terminate_execution() {
        let state = test_state();
        thread::scope(|scope| {
            scope.spawn(|| {
                thread::sleep(Duration::from_millis(100));
                state.cancel.store(true, Ordering::SeqCst);
            });
            let result = run("sleep(60000);", &state);
            assert_eq!(result["reason"], "Stopped");
            assert!(result["elapsedMs"].as_u64().unwrap() < 3000);
        });
        state.cancel.store(false, Ordering::SeqCst);
        let result = run(
            &format!("while (true) {{ echo \"{}\"; }}", "x".repeat(1000)),
            &state,
        );
        assert_eq!(result["reason"], "Output exceeded 1 MiB");
        assert!(result["stdout"].as_str().unwrap().len() <= MAX_OUTPUT as usize);
    }

    #[test]
    fn wall_clock_limit_stops_blocking_script() {
        let result = run("sleep(60000);", &test_state());
        assert_eq!(result["reason"], "Execution exceeded 10 seconds");
    }
}
