# Development

See the [README](../README.md) for launch instructions.

## Project layout

The playground lives outside the Scripta interpreter repository and Cargo
workspace. Its editor, server, dependencies, and downloads are not included in
normal Scripta builds or interpreter release archives.

## Interpreter configuration

The primary Scripta executable is `scripta`; the compatibility name `scr` is also
supported. With an interpreter containing the optional launcher,
`scripta playground` launches the separately installed companion and passes the
current interpreter's absolute path. It does not download or install anything.
Use `scripta playground --no-open` to keep the browser from opening automatically.

Set `SCR_PLAYGROUND_BIN` to the companion's full executable path to override its
location. Direct launch with `scr-playground --scr /path/to/scripta` selects an
interpreter explicitly; otherwise it searches `SCR_BIN`, the bundled runtime,
adjacent executables, and PATH, in that order, preferring `scripta` over `scr` in
each directory.

Uninstall by deleting the extracted playground folder and removing its PATH
entry or environment override.

## Execution behavior and limits

Each run starts a fresh interpreter in a temporary working directory with closed
standard input. Output appears after execution completes. Relative files are
removed afterward; absolute paths and host system access retain ordinary
Scripta behavior.

The server binds only to 127.0.0.1 on a random port. Host and Origin validation
and a random session token protect execution requests from unrelated web pages.
Scripts still have your account's filesystem, network, and process permissions.
Do not proxy or expose the server publicly.

Runs stop after 10 seconds, around 1 MiB of captured output, or the interpreter's
step/value/scope limits. Output is polled, so temporary files can exceed the
capture threshold between checks. Value limits are not a hard process-memory
ceiling. Stop terminates the interpreter's process group on Unix or Job Object
on Windows. Detached processes or subprocesses that create independent groups
can escape cleanup; run only trusted local scripts.

## Build and check

Install a Rust toolchain supporting edition 2024, and build the `scripta` interpreter
from the [Scripta repository](https://github.com/trafficinc/scripta).
From the playground project directory:

```sh
cargo build --locked --release
SCR_TEST_BIN=/absolute/path/to/scripta cargo test --locked
python3 -m unittest discover -s tests -p 'test_*.py'
target/release/scr-playground --scr /absolute/path/to/scripta
python3 tests/http_smoke.py --binary target/release/scr-playground --scr /absolute/path/to/scripta
```

The execution tests require `SCR_TEST_BIN`; HTTP smoke additionally requires
permission to bind loopback sockets. `--no-open` keeps tests from opening a tab.

## Scan for secrets

Install Gitleaks with `brew install gitleaks`, then run from the project directory:

```sh
./scripts/scan-secrets.sh              # Git history across all local refs
./scripts/scan-secrets.sh working-tree # Current files, including untracked/ignored files
./scripts/scan-secrets.sh staged       # Staged changes before committing
```

The script works from any directory, redacts detected secrets, and returns a
nonzero exit status if secrets are found or the scan fails. The working-tree
scan also includes local build output; it does not scan Git history.

## Release packaging

### Standalone executable

Install Rust through rustup, Python 3.9+, and native C build tools (Xcode Command
Line Tools on macOS, a C compiler on Linux, or Visual Studio C++ Build Tools on
Windows). Cargo dependencies must be cached or downloadable. End users only need
the resulting executable.

For a fresh checkout, clone the repositories into sibling folders:

```sh
git clone https://github.com/trafficinc/scripta.git
git clone https://github.com/trafficinc/scripta-playground.git
cd scripta-playground
python3 scripts/build-standalone.py
```

The script uses Scripta's pinned Rust toolchain; rustup may download it on the
first build. It prints the executable path when finished. For Apple Silicon
macOS 15+, launch with:

```sh
./dist/standalone-aarch64-apple-darwin/scr-playground
```

By default the build uses `../scripta`. For a different source location:

```sh
python3 scripts/build-standalone.py --scripta-source /path/to/scripta
```

Only Apple Silicon macOS has been validated so far; Linux and Windows validation
is pending.

The helper builds Scripta with its pinned toolchain and default extensions,
then embeds it and the collected license notices into a playground release
build. Files are generated under this repository's `target/` and `dist/` folders.
The executable and SHA-256 sidecar are in `dist/standalone-<native-target>/`.
Build natively on each supported OS and processor; this helper does not cross-compile.
macOS builds target macOS 15 or newer. Signing and notarization are separate steps.

At startup, `--scr` explicitly selects an interpreter and errors if unusable.
Otherwise the app checks `SCR_BIN`, the adjacent `runtime/` folder, adjacent
executables, then PATH. Candidates must run successfully and report the same
major/minor Scripta version as the bundled copy. Invalid or incompatible
candidates are skipped. If none qualifies, it extracts the embedded interpreter
to a private temporary directory, executes it as a child process, and removes it
on normal shutdown. Installed interpreters avoid extraction; interpreter bytes
remain part of the download. `--licenses` prints the embedded license notices.

### Manual GitHub Actions builds

Commit and push the workflow and supporting files to the default branch. On
GitHub, open **Actions → Standalone builds and releases → Run workflow**, choose
the playground branch, and fill in:

| Input | Value |
| --- | --- |
| Version | `0.1.0` or `0.2.0-rc.1`, without a leading `v` |
| Release option | **Build only**, **Prerelease**, or **Release** |
| Scripta ref | Interpreter branch, tag, or commit; defaults to `main` |

Click **Run workflow**. There are no automatic push, tag, or pull-request triggers.
The Scripta ref is resolved once; every job checks out that same commit. The
entered version is stamped into `Cargo.toml` and the application's `Cargo.lock`
entry only in the temporary runner checkout. Dependency pins are preserved, and
no version changes are committed or pushed back. The executable's `--version`,
asset names, and release tag use the entered version.

Native jobs build Apple Silicon macOS 15+, Windows x64, and Linux x64/ARM64 on
Ubuntu 22.04. Each runs Rust tests with and without the embedded interpreter,
Clippy, Python tests, and HTTP smoke tests with both installed and embedded
interpreters. It also checks the executable version and embedded notices.
Cross-platform support remains pending until the corresponding Actions jobs pass.

After all four jobs pass, the run summary links to the complete downloadable
artifact, retained for 30 days. It contains macOS/Linux `.tar.gz` archives (each
with one executable), the Windows `.exe`, `SHA256SUMS`, and `build.json` recording
source commits and validation. Unix archives preserve executable permissions.
Extract one, then run `./scr-playground`; on Windows run the downloaded `.exe`.

**Build only** creates neither tags nor releases. **Prerelease** publishes a
GitHub prerelease tagged `v<version>`. **Release** publishes a normal release and
rejects versions containing prerelease suffixes. Publishing uses this run's
saved, checksum-verified artifacts, without rebuilding. Write permission is
granted only to the publishing job; the built-in `GITHUB_TOKEN` is used.

Existing tags or releases are not overwritten. A failed upload can leave a
draft release; remove that incomplete draft before retrying the version. These
builds are unsigned; signing and macOS notarization are separate steps.

### Archive with separate interpreter

Downloads must be built and tested on each supported platform before publishing.
The platform launchers are local scripts, not signed native app installers.

First build and validate a normal Scripta interpreter archive with Scripta's
existing release procedure. Then build this project natively for the same target
and package it independently:

```sh
python3 scripts/package.py \
  --binary target/release/scr-playground \
  --runtime /path/to/scripta-0.1.1-aarch64-apple-darwin.tar.gz \
  --target aarch64-apple-darwin \
  --notices-dir /path/to/scripta/release-notices
```

Python is used only by maintainers for packaging and smoke tests. Packaging checks
the interpreter's inventory and hashes, preserves its complete notices/docs,
collects this app's dependency licenses, and writes a separate archive and SHA-256
sidecar under `dist/`. `--notices-dir` supplies reviewed upstream license
supplements when a locked crate omits them; provenance and hashes are checked.
Target dependencies must be available in Cargo's local cache before packaging.
The normal interpreter release process never includes this sibling project.

Publish this project in its own repository and publish its downloads separately.
No remote repository or public release is created by the local build scripts.

