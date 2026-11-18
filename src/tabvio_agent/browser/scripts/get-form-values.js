(() => {
  const SKIPPED_TYPES = ['hidden', 'submit', 'button', 'reset', 'image', 'file'];
  // Kept in step with scan-page.js: a private value is never handed to the agent.
  const PRIVATE_INPUT_TYPES = ['password', 'email', 'tel'];
  const PRIVATE_FIELD_WORDS =
    /(^|[^a-z])(card|cvv|cvc|csc|ssn|sin|iban|swift|routing|account|passcode|pin|secret|token)([^a-z]|$)/i;

  function isPrivate(control) {
    if (control.dataset.tabvioSensitive) return true;
    if (PRIVATE_INPUT_TYPES.includes(control.type)) return true;

    const autocomplete = (control.getAttribute('autocomplete') || '').toLowerCase();
    if (autocomplete.startsWith('cc-')) return true;

    const identity = [
      control.getAttribute('name'),
      control.id,
      control.getAttribute('aria-label')
    ].join(' ');
    return PRIVATE_FIELD_WORDS.test(identity);
  }

  function isVisible(control) {
    const style = window.getComputedStyle(control);
    if (style.display === 'none' || style.visibility === 'hidden') {
      return false;
    }
    const box = control.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  }

  function labelOf(control) {
    const ariaLabel = control.getAttribute('aria-label');
    if (ariaLabel && ariaLabel.trim()) {
      return ariaLabel.trim();
    }
    if (control.labels && control.labels.length > 0 && control.labels[0].innerText.trim()) {
      return control.labels[0].innerText.trim();
    }
    const placeholder = control.getAttribute('placeholder');
    if (placeholder && placeholder.trim()) {
      return placeholder.trim();
    }
    return control.getAttribute('name') || control.id || control.tagName.toLowerCase();
  }

  function valueOf(control, type) {
    if (control.tagName === 'SELECT') {
      const option = control.options[control.selectedIndex];
      return option ? option.text.trim() : '';
    }
    if (type === 'checkbox' || type === 'radio') {
      return control.checked ? 'checked' : 'unchecked';
    }
    return control.value;
  }

  const fields = [];
  for (const control of document.querySelectorAll('input, textarea, select')) {
    const type = (control.getAttribute('type') || '').toLowerCase();
    if (SKIPPED_TYPES.includes(type) || !isVisible(control)) {
      continue;
    }
    let value = valueOf(control, type);
    if (value.trim() && isPrivate(control)) {
      value = 'filled';
    }
    fields.push({label: labelOf(control), value: value});
  }
  return fields;
})()
