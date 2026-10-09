import AppKit
import Darwin
import Foundation
import WebKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, NSSpeechSynthesizerDelegate {
    private enum HealthResult {
        case healthy
        case starting
        case unavailable
        case anotherService
    }

    private var window: NSWindow!
    private var webView: WKWebView!
    private var auxiliaryWindows: [(window: NSWindow, webView: WKWebView)] = []
    private var startupPane: NSView!
    private var startupLabel: NSTextField!
    private var startupSpinner: NSProgressIndicator!
    private var retryButton: NSButton!
    private var backendProcess: Process?
    private var backendLogHandles: (FileHandle, FileHandle)?
    private var ownsBackend = false
    private var hasLoadedGuide = false
    private var startupAttempts = 0
    private var speechSynthesizer: NSSpeechSynthesizer?
    private weak var speechWebView: WKWebView?
    private var startupActivity: NSObjectProtocol?
    private let session = URLSession(configuration: .ephemeral)

    private var configuration: [String: Any] {
        Bundle.main.infoDictionary ?? [:]
    }

    private var serverPath: String {
        configuration["StudyAppServerPath"] as? String ?? ""
    }

    private var pythonPath: String {
        configuration["StudyAppPythonPath"] as? String ?? ""
    }

    private var workingDirectory: String {
        configuration["StudyAppWorkingDirectory"] as? String ?? ""
    }

    private var appURL: URL {
        URL(string: configuration["StudyAppURL"] as? String ?? "http://127.0.0.1:8770/")!
    }

    private var healthURL: URL {
        appURL.appending(path: "health")
    }

    private var launcherLogURL: URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return support.appendingPathComponent("LLU Study", isDirectory: true)
            .appendingPathComponent("launcher.log", isDirectory: false)
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        installMenu()
        buildWindow()
        beginStartupActivity()
        appendLauncherLog("App launched; checking local service at \(appURL.absoluteString)")
        checkExistingService()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        speechSynthesizer?.stopSpeaking()
        speechSynthesizer = nil
        if ownsBackend, let process = backendProcess, process.isRunning {
            appendLauncherLog("App terminating; stopping app-owned server process \(process.processIdentifier)")
            process.terminate()
        }
        endStartupActivity()
    }

    private func buildWindow() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.userContentController.add(self, name: "nativeSpeak")

        let contentSize = NSSize(width: 1320, height: 900)
        webView = WKWebView(frame: NSRect(origin: .zero, size: contentSize), configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        webView.autoresizingMask = [.width, .height]
        webView.isHidden = true

        let root = NSView(frame: NSRect(origin: .zero, size: contentSize))
        root.autoresizingMask = [.width, .height]
        root.wantsLayer = true
        root.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        root.addSubview(webView)

        startupPane = NSView(frame: root.bounds)
        startupPane.autoresizingMask = [.width, .height]
        startupPane.wantsLayer = true
        startupPane.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        root.addSubview(startupPane)

        startupSpinner = NSProgressIndicator()
        startupSpinner.translatesAutoresizingMaskIntoConstraints = false
        startupSpinner.style = .spinning
        startupSpinner.controlSize = .regular
        startupSpinner.startAnimation(nil)

        startupLabel = NSTextField(wrappingLabelWithString: "Checking the local study service…")
        startupLabel.translatesAutoresizingMaskIntoConstraints = false
        startupLabel.alignment = .center
        startupLabel.font = .systemFont(ofSize: 15, weight: .medium)

        retryButton = NSButton(title: "Retry", target: self, action: #selector(retryStartup))
        retryButton.translatesAutoresizingMaskIntoConstraints = false
        retryButton.isHidden = true

        startupPane.addSubview(startupSpinner)
        startupPane.addSubview(startupLabel)
        startupPane.addSubview(retryButton)
        NSLayoutConstraint.activate([
            startupSpinner.centerXAnchor.constraint(equalTo: startupPane.centerXAnchor),
            startupSpinner.centerYAnchor.constraint(equalTo: startupPane.centerYAnchor, constant: -38),
            startupLabel.centerXAnchor.constraint(equalTo: startupPane.centerXAnchor),
            startupLabel.topAnchor.constraint(equalTo: startupSpinner.bottomAnchor, constant: 18),
            startupLabel.widthAnchor.constraint(lessThanOrEqualToConstant: 480),
            retryButton.centerXAnchor.constraint(equalTo: startupPane.centerXAnchor),
            retryButton.topAnchor.constraint(equalTo: startupLabel.bottomAnchor, constant: 18)
        ])

        window = NSWindow(
            contentRect: NSRect(origin: .zero, size: contentSize),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.title = "LLU Study"
        window.minSize = NSSize(width: 820, height: 600)
        window.center()
        window.contentView = root
        window.delegate = self
        root.needsLayout = true
        root.setNeedsDisplay(root.bounds)
        window.makeKeyAndOrderFront(nil)
        window.displayIfNeeded()
        NSApp.activate(ignoringOtherApps: true)
    }

    private func installMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(title: "Quit LLU Study", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu

        let editItem = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        editItem.submenu = editMenu
        mainMenu.addItem(editItem)

        let navigationItem = NSMenuItem(title: "Navigate", action: nil, keyEquivalent: "")
        let navigationMenu = NSMenu(title: "Navigate")
        let back = NSMenuItem(title: "Back", action: #selector(goBack(_:)), keyEquivalent: "[")
        back.target = self
        let forward = NSMenuItem(title: "Forward", action: #selector(goForward(_:)), keyEquivalent: "]")
        forward.target = self
        navigationMenu.addItem(back)
        navigationMenu.addItem(forward)
        navigationMenu.addItem(NSMenuItem.separator())
        let reload = NSMenuItem(title: "Reload", action: #selector(reloadPage(_:)), keyEquivalent: "r")
        reload.target = self
        navigationMenu.addItem(reload)
        navigationItem.submenu = navigationMenu
        mainMenu.addItem(navigationItem)
        NSApp.mainMenu = mainMenu
    }

    @objc private func goBack(_ sender: Any?) {
        if webView.canGoBack { webView.goBack() }
    }

    @objc private func goForward(_ sender: Any?) {
        if webView.canGoForward { webView.goForward() }
    }

    @objc private func reloadPage(_ sender: Any?) {
        webView.reload()
    }

    @objc private func retryStartup() {
        startupAttempts = 0
        retryButton.isHidden = true
        startupSpinner.startAnimation(nil)
        beginStartupActivity()
        setStartupMessage("Checking the local study service…")
        if ownsBackend, let process = backendProcess, process.isRunning {
            appendLauncherLog("Retry requested; rechecking existing app-owned process \(process.processIdentifier)")
            waitForService()
            return
        }
        checkExistingService()
    }

    private func checkExistingService() {
        probeHealth { [weak self] result in
            guard let self else { return }
            switch result {
            case .healthy:
                self.openGuide()
            case .starting:
                self.waitForService()
            case .unavailable:
                self.startOwnedService()
            case .anotherService:
                self.showStartupError("Port 8770 is responding, but it is not the LLU Study service. Close the other service before opening this app.")
            }
        }
    }

    private func startOwnedService() {
        guard FileManager.default.isExecutableFile(atPath: pythonPath) else {
            showStartupError("The configured study Python is missing or not executable:\n\(pythonPath)")
            return
        }
        guard FileManager.default.fileExists(atPath: serverPath) else {
            showStartupError("The local study service script is missing:\n\(serverPath)")
            return
        }

        setStartupMessage("Starting the local study service…")
        appendLauncherLog("Starting server; python=\(pythonPath), script=\(serverPath), cwd=\(workingDirectory)")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonPath)
        process.arguments = [serverPath]
        process.currentDirectoryURL = URL(fileURLWithPath: workingDirectory, isDirectory: true)
        process.qualityOfService = .userInitiated
        do {
            let handles = try makeBackendLogHandles()
            backendLogHandles = handles
            process.standardOutput = handles.0
            process.standardError = handles.1
        } catch {
            showStartupError("Could not open the private server log: \(error.localizedDescription)")
            return
        }
        process.terminationHandler = { [weak self] child in
            Task { @MainActor in
                guard let self, self.ownsBackend, self.backendProcess === child, !self.hasLoadedGuide else { return }
                self.appendLauncherLog("App-owned server exited before readiness (exit \(child.terminationStatus))")
                self.ownsBackend = false
                self.showStartupError("The local study service stopped before it became ready (exit code \(child.terminationStatus)).", includeDiagnostics: true)
            }
        }

        do {
            backendProcess = process
            ownsBackend = true
            try process.run()
            startupAttempts = 0
            waitForService()
        } catch {
            backendProcess = nil
            ownsBackend = false
            appendLauncherLog("Failed to run server process: \(error.localizedDescription)")
            showStartupError("Could not start the local study service: \(error.localizedDescription)", includeDiagnostics: true)
        }
    }

    private func waitForService() {
        guard !hasLoadedGuide else { return }
        if ownsBackend, let backendProcess, !backendProcess.isRunning {
            showStartupError("The local study service stopped before it became ready.")
            return
        }
        startupAttempts += 1
        if startupAttempts > 120 {
            appendLauncherLog("Timed out waiting for the local health endpoint after \(startupAttempts) probes")
            if ownsBackend, let process = backendProcess, process.isRunning {
                appendLauncherLog("Stopping timed-out app-owned server process \(process.processIdentifier)")
                process.terminate()
            }
            showStartupError("The local study service did not become ready. Recent server output is shown below.", includeDiagnostics: true)
            return
        }
        probeHealth { [weak self] result in
            guard let self else { return }
            switch result {
            case .healthy:
                self.openGuide()
            case .anotherService:
                self.showStartupError("Port 8770 is occupied by a service that is not LLU Study. It was left untouched.")
            case .starting, .unavailable:
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.waitForService() }
            }
        }
    }

    private func probeHealth(completion: @escaping (HealthResult) -> Void) {
        var request = URLRequest(url: healthURL, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 2.0)
        request.httpMethod = "GET"
        session.dataTask(with: request) { data, response, error in
            let result: HealthResult
            if error != nil || response == nil {
                result = .unavailable
            } else if let data,
                      let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                      json["app"] as? String == "llu-study-app" {
                result = (json["ready"] as? Bool == true) ? .healthy : .starting
            } else {
                result = .anotherService
            }
            DispatchQueue.main.async { completion(result) }
        }.resume()
    }

    private func openGuide() {
        guard !hasLoadedGuide else { return }
        hasLoadedGuide = true
        appendLauncherLog("Health check succeeded; loading app URL")
        endStartupActivity()
        startupSpinner.stopAnimation(nil)
        setStartupMessage("Opening your study guide…")
        webView.load(URLRequest(url: appURL))
    }

    private func showStartupError(_ message: String, includeDiagnostics: Bool = false) {
        startupSpinner.stopAnimation(nil)
        endStartupActivity()
        setStartupMessage(message + (includeDiagnostics ? backendDiagnostics() : ""))
        retryButton.isHidden = false
    }

    private func setStartupMessage(_ message: String) {
        startupLabel.stringValue = message
    }

    private func beginStartupActivity() {
        guard startupActivity == nil else { return }
        startupActivity = ProcessInfo.processInfo.beginActivity(options: [.userInitiated],
                                                                 reason: "Starting the local LLU Study service")
    }

    private func endStartupActivity() {
        guard let activity = startupActivity else { return }
        ProcessInfo.processInfo.endActivity(activity)
        startupActivity = nil
    }

    private func makeBackendLogHandles() throws -> (FileHandle, FileHandle) {
        let directory = launcherLogURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        _ = directory.path.withCString { Darwin.chmod($0, mode_t(S_IRWXU)) }
        let flags = O_WRONLY | O_CREAT | O_APPEND
        let mode = mode_t(S_IRUSR | S_IWUSR)
        let outputFD = launcherLogURL.path.withCString { Darwin.open($0, flags, mode) }
        guard outputFD >= 0 else {
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno),
                          userInfo: [NSLocalizedDescriptionKey: "Cannot open \(launcherLogURL.path)"])
        }
        _ = launcherLogURL.path.withCString { Darwin.chmod($0, mode) }
        let errorFD = Darwin.dup(outputFD)
        guard errorFD >= 0 else {
            Darwin.close(outputFD)
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(errno),
                          userInfo: [NSLocalizedDescriptionKey: "Cannot duplicate the server log handle"])
        }
        return (FileHandle(fileDescriptor: outputFD, closeOnDealloc: true),
                FileHandle(fileDescriptor: errorFD, closeOnDealloc: true))
    }

    private func appendLauncherLog(_ message: String) {
        do {
            let directory = launcherLogURL.deletingLastPathComponent()
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            _ = directory.path.withCString { Darwin.chmod($0, mode_t(S_IRWXU)) }
            let fd = launcherLogURL.path.withCString { Darwin.open($0, O_WRONLY | O_CREAT | O_APPEND, mode_t(S_IRUSR | S_IWUSR)) }
            guard fd >= 0 else { return }
            _ = launcherLogURL.path.withCString { Darwin.chmod($0, mode_t(S_IRUSR | S_IWUSR)) }
            let handle = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
            let stamp = ISO8601DateFormatter().string(from: Date())
            try handle.write(contentsOf: Data("[\(stamp)] \(message)\n".utf8))
            try handle.close()
        } catch {
            // Startup must remain available even when diagnostics cannot be saved.
        }
    }

    private func backendDiagnostics() -> String {
        guard let content = try? String(contentsOf: launcherLogURL, encoding: .utf8) else {
            return "\n\nLog file: \(launcherLogURL.path)"
        }
        let tail = content.split(whereSeparator: \.isNewline).suffix(12).joined(separator: "\n")
        let clipped = String(tail.suffix(1800))
        return "\n\nRecent server output:\n\(clipped.isEmpty ? "(No server output yet.)" : clipped)\n\nLog file: \(launcherLogURL.path)"
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView === self.webView {
            startupPane.isHidden = true
            self.webView.isHidden = false
            self.webView.needsLayout = true
            self.webView.setNeedsDisplay(self.webView.bounds)
            self.webView.layoutSubtreeIfNeeded()
            window.contentView?.layoutSubtreeIfNeeded()
            window.displayIfNeeded()
            window.title = webView.title?.isEmpty == false ? "\(webView.title!) · LLU Study" : "LLU Study"
        } else if let childWindow = auxiliaryWindows.first(where: { $0.webView === webView })?.window {
            childWindow.title = webView.title?.isEmpty == false ? "\(webView.title!) · LLU Study" : "LLU Study"
        }
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if webView === self.webView { showStartupError("The study guide could not load: \(error.localizedDescription)") }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if webView === self.webView { showStartupError("The study guide could not load: \(error.localizedDescription)") }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }
        if url.host == appURL.host && url.port == appURL.port {
            decisionHandler(.allow)
        } else if navigationAction.targetFrame == nil {
            if url.scheme == "http" || url.scheme == "https" { NSWorkspace.shared.open(url) }
            decisionHandler(.cancel)
        } else {
            decisionHandler(.allow)
        }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard navigationAction.targetFrame == nil else { return nil }
        guard let url = navigationAction.request.url else { return nil }
        if url.host == appURL.host && url.port == appURL.port {
            let childSize = NSSize(width: 900, height: 760)
            let childWebView = WKWebView(frame: NSRect(origin: .zero, size: childSize), configuration: configuration)
            childWebView.navigationDelegate = self
            childWebView.uiDelegate = self
            childWebView.allowsBackForwardNavigationGestures = true
            childWebView.autoresizingMask = [.width, .height]

            let childWindow = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 900, height: 760),
                styleMask: [.titled, .closable, .miniaturizable, .resizable],
                backing: .buffered,
                defer: false
            )
            childWindow.title = "LLU Study"
            childWindow.minSize = NSSize(width: 640, height: 480)
            childWindow.center()
            childWindow.contentView = childWebView
            childWindow.delegate = self
            auxiliaryWindows.append((childWindow, childWebView))
            childWindow.makeKeyAndOrderFront(nil)
            return childWebView
        } else if url.scheme == "http" || url.scheme == "https" {
            NSWorkspace.shared.open(url)
        }
        return nil
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        let payload = message.body as? String ?? ((message.body as? [String: Any])?["text"] as? String ?? "")
        if payload == "stop" || (message.body as? [String: Any])?["stop"] as? Bool == true {
            speechSynthesizer?.stopSpeaking()
            speechSynthesizer = nil
            notifySpeechEnded(speechWebView ?? message.webView)
            return
        }
        speakLocally(payload, in: message.webView)
    }

    func windowWillClose(_ notification: Notification) {
        guard let closingWindow = notification.object as? NSWindow,
              closingWindow !== window else { return }
        auxiliaryWindows.removeAll { $0.window === closingWindow }
    }

    private func speakLocally(_ rawText: String, in sourceWebView: WKWebView?) {
        let text = rawText.split(whereSeparator: \.isWhitespace).joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, text.count <= 180, text.split(separator: " ").count <= 24 else { return }
        if speechSynthesizer?.isSpeaking == true {
            speechSynthesizer?.stopSpeaking()
            speechSynthesizer = nil
            notifySpeechEnded(speechWebView)
            return
        }

        let englishVoice = NSSpeechSynthesizer.availableVoices.first { voice in
            let locale = NSSpeechSynthesizer.attributes(forVoice: voice)[.localeIdentifier] as? String
            return locale?.lowercased().hasPrefix("en") == true
        }
        guard let englishVoice, let synthesizer = NSSpeechSynthesizer(voice: englishVoice) else { return }
        speechSynthesizer = synthesizer
        speechWebView = sourceWebView
        synthesizer.delegate = self
        synthesizer.rate = 155
        let expanded = text.replacingOccurrences(of: #"\bIFN\s*[-–]?\s*(?:beta|β)(?!\w)"#, with: "interferon beta", options: .regularExpression)
        synthesizer.startSpeaking(expanded)
    }

    func speechSynthesizer(_ sender: NSSpeechSynthesizer, didFinishSpeaking finishedSpeaking: Bool) {
        if sender === speechSynthesizer {
            speechSynthesizer = nil
            notifySpeechEnded(speechWebView)
        }
    }

    private func notifySpeechEnded(_ target: WKWebView?) {
        speechWebView = nil
        target?.evaluateJavaScript("window.referenceNativeSpeechEnded && window.referenceNativeSpeechEnded()", completionHandler: nil)
    }
}

MainActor.assumeIsolated {
    let application = NSApplication.shared
    let appDelegate = AppDelegate()
    application.setActivationPolicy(.regular)
    application.delegate = appDelegate
    application.run()
}
