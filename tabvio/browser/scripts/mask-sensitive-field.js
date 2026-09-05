(({x, y}) => {
    const hit = document.elementFromPoint(x, y);
    const field = hit?.closest('input, textarea');
    if (!field) throw new Error('Sensitive target is not an input');

    field.dataset.tabvioSensitive = 'true';
    field.style.setProperty('-webkit-text-security', 'disc', 'important');
})
