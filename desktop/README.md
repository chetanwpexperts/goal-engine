# DBaba desktop

Open your own DBaba installation from a desktop window. The app does not install the server, check for updates, or send usage reports.

## Build

Install Node.js 22, npm and stable Rust (cargo). Install the system OpenSSH client, including ssh-keyscan and ssh-keygen, to use SSH connections.

- macOS: install Xcode command line tools with `xcode-select --install`.
- Windows: install Microsoft C++ Build Tools (Desktop development with C++) and WebView2 Runtime. Enable the OpenSSH Client optional feature.
- Ubuntu 22.04/24.04 or Debian: install build-essential, curl, wget, file, libwebkit2gtk-4.1-dev, libappindicator3-dev, librsvg2-dev, patchelf, libxdo-dev, libssl-dev, libdbus-1-dev and openssh-client. A running Secret Service provider and a desktop tray implementation are needed.

From the repository root:

```sh
cd desktop
npm install --no-audit --no-fund
npm run tauri -- icon icon.svg
cargo test --manifest-path src-tauri/Cargo.toml
npm run tauri -- dev
npm run tauri -- build
```

Installers are under src-tauri/target/release/bundle. The supplied ci-build.yml is intentionally not active until moved into the repository workflow folder. The workflow produces unsigned build artifacts, not signed or notarized releases.

## Use

Start DBaba separately. Add a name and its port (8765 by default). For a remote installation, enter the SSH host, user and absolute private key path. Load passphrase-protected keys into ssh-agent first. No SSH password is collected. The app uses its own known_hosts file in the OS app-config folder; compare an unknown Ed25519 fingerprint with your administrator before accepting. Changed keys are rejected; ask your administrator before changing trusted keys. Only Ed25519 host key discovery is supported in this version.

Add an optional API key for alert requests. It is saved in the OS keychain, never in connections.json. On Linux unlock your Secret Service collection first. The web screen uses DBaba's own login; the key is not inserted into the web page. Open a connection to show the server screen in a separate app window. External links open in your normal browser. Disconnect or close the server window to stop polling and close the tunnel. Use Quit DBaba in the tray to close all tunnels.

The desktop polls GET /api/state every 60 seconds while a connection is open, without redirects or system proxies. Warning and critical alerts are shown once per connection and alert ID when not acknowledged. Seen IDs are saved locally. If the server does not supply alerts, nothing is shown. Notification availability depends on the OS. No additional API calls are made by the connection manager; the server's own web screen makes its normal requests.

If the server is an older release without screen password protection, keep it reachable only through loopback or SSH, and limit access to trusted users.
