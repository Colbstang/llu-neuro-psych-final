/* Manual lookup works even when a word is absent from the scanner vocabulary. */
(function (scope) {
  'use strict';
  function validSelection(text) { return typeof text === 'string' && !!text.trim() && text.trim().length <= 160; }
  function currentSelection(root, selection = getSelection()) {
    const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
    return range && root.contains(range.commonAncestorContainer) ? selection.toString().trim() : '';
  }
  function attachSelectionLookup(root, onLookup) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'selection-define'; button.textContent = 'Define · D'; button.hidden = true;
    button.setAttribute('aria-label', 'Define selected medical term'); document.body.append(button);
    let selected = '', timer;
    function update() {
      const selection = getSelection(), range = selection?.rangeCount ? selection.getRangeAt(0) : null;
      selected = currentSelection(root, selection);
      button.hidden = !validSelection(selected);
      if (!button.hidden) {
        const rect = range.getBoundingClientRect();
        button.style.left = Math.max(8, Math.min(rect.left, innerWidth - 130)) + 'px';
        button.style.top = Math.max(8, Math.min(rect.bottom + 6, innerHeight - 42)) + 'px';
      }
    }
    function changed() { clearTimeout(timer); timer = setTimeout(update, 90); }
    function define() { if (validSelection(selected)) { const text = selected; button.hidden = true; onLookup(text); } }
    function key(event) {
      if (event.key === 'Escape') button.hidden = true;
      if (event.key.toLowerCase() === 'd' && !event.ctrlKey && !event.metaKey && !event.altKey &&
          !event.target.closest?.('input,textarea,select,[contenteditable="true"]')) {
        // selectionchange can be deferred; the keyboard shortcut must use the
        // selection visible at keydown, not the previous debounced snapshot.
        update();
        if (validSelection(selected)) { event.preventDefault(); define(); }
      }
    }
    button.addEventListener('pointerdown', event => event.preventDefault()); button.onclick = define;
    document.addEventListener('selectionchange', changed); document.addEventListener('keydown', key);
    const hide = () => { button.hidden = true; };
    addEventListener('scroll', hide, true); addEventListener('resize', hide);
    return {destroy() { clearTimeout(timer); document.removeEventListener('selectionchange', changed); document.removeEventListener('keydown', key); removeEventListener('scroll', hide, true); removeEventListener('resize', hide); button.remove(); }};
  }
  scope.MedicalSelectionLookup = {attach: attachSelectionLookup, validSelection, currentSelection};
  if (typeof module !== 'undefined' && module.exports) module.exports = scope.MedicalSelectionLookup;
})(typeof window !== 'undefined' ? window : this);
