({x, y}) => {
    // Called with the coordinates of a stored element, before anything secret
    // is typed into it. Marking the field makes browser captures show discs
    // instead of the value, so the secret never reaches a stored frame.
    const hit = document.elementFromPoint(x, y);
    const field = hit?.closest('input, textarea');
    if (!field) throw new Error('Sensitive target is not an input');

    field.dataset.tabvioSensitive = 'true';
    field.style.setProperty('-webkit-text-security', 'disc', 'important');
}
