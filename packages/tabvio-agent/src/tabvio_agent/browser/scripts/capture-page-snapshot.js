(() => {
    const snapshotLines = [];
    const elements = document.body ? document.body.querySelectorAll('*') : [];
    const maxElements = 3000;
    let inspectedElements = 0;

    for (const element of elements) {
        if (inspectedElements >= maxElements) break;
        inspectedElements += 1;

        const bounds = element.getBoundingClientRect();
        if (bounds.width <= 0 || bounds.height <= 0) continue;

        const outsideViewport = bounds.bottom <= 0 || bounds.right <= 0 ||
            bounds.top >= innerHeight || bounds.left >= innerWidth;
        if (outsideViewport) continue;

        const style = getComputedStyle(element);
        if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') {
            continue;
        }

        if (element.tagName === 'INPUT') {
            const checkedState = element.checked ? 'on' : 'off';
            snapshotLines.push(`input|${element.type}|${checkedState}|${element.value}`);
        } else if (element.tagName === 'SELECT') {
            snapshotLines.push(`select|${element.value}`);
        } else if (element.tagName === 'TEXTAREA') {
            snapshotLines.push(`textarea|${element.value}`);
        } else {
            const ownText = Array.from(element.childNodes)
                .filter(node => node.nodeType === Node.TEXT_NODE)
                .map(node => node.textContent.trim())
                .filter(Boolean)
                .join(' ');

            if (ownText) snapshotLines.push(ownText);
        }
    }

    return snapshotLines.join('\n');
})
