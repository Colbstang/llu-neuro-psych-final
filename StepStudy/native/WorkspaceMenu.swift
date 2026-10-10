// Included only in Step Study's transformed launcher, never LLU Study.
extension AppDelegate {
    func installWorkspaceMenu(_ mainMenu: NSMenu) {
        let item = NSMenuItem(title: "Study", action: nil, keyEquivalent: "")
        let menu = NSMenu(title: "Study")
        let capture = NSMenuItem(title: "Capture region into question inbox", action: #selector(captureStudyRegion(_:)), keyEquivalent: "2")
        capture.keyEquivalentModifierMask = [.command, .shift]
        capture.target = self
        menu.addItem(capture)
        item.submenu = menu
        mainMenu.addItem(item)
    }

    @objc private func captureStudyRegion(_ sender: Any?) {
        // Main-window context is retained even when the course frame has focus.
        guard let view = webView else { return }
        view.evaluateJavaScript("window.StepWorkspaceTools?.captureRegion()", completionHandler: nil)
    }
}
