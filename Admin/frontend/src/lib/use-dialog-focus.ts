import { useEffect, useRef, type RefObject } from 'react';

/** Keep keyboard focus in an open modal and return it to its invoking control. */
export function useDialogFocus(open: boolean, root: RefObject<HTMLElement | null>, close: () => void, busy = false,
    returnFallback?: RefObject<HTMLElement | null>) {
    const behavior = useRef({ close, busy });
    useEffect(() => { behavior.current = { close, busy }; }, [close, busy]);
    useEffect(() => {
        const dialog = root.current;
        if (!open || !dialog) return;
        const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        const overflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        const background: Array<[Element, string | null]> = [];
        let branch: Element = dialog;
        while (branch.parentElement && branch !== document.body) {
            for (const sibling of branch.parentElement.children) {
                if (sibling !== branch) {
                    background.push([sibling, sibling.getAttribute('inert')]);
                    sibling.setAttribute('inert', '');
                }
            }
            branch = branch.parentElement;
        }
        const targets = () => [...dialog.querySelectorAll<HTMLElement>(
            'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]',
        )].filter((node) => !node.closest('[hidden], [inert]') && window.getComputedStyle(node).display !== 'none' && window.getComputedStyle(node).visibility !== 'hidden');
        const focusFirst = () => (dialog.querySelector<HTMLElement>('[data-dialog-autofocus]') ?? targets()[0] ?? dialog).focus();
        const keydown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                if (!behavior.current.busy) behavior.current.close();
            } else if (event.key === 'Tab') {
                const items = targets(), first = items[0], last = items.at(-1);
                if (!first) { event.preventDefault(); dialog.focus(); }
                else if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) {
                    event.preventDefault(); last?.focus();
                } else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) {
                    event.preventDefault(); first.focus();
                }
            }
        };
        const focusin = (event: FocusEvent) => { if (!dialog.contains(event.target as Node)) focusFirst(); };
        document.addEventListener('keydown', keydown);
        document.addEventListener('focusin', focusin);
        focusFirst();
        return () => {
            document.removeEventListener('keydown', keydown);
            document.removeEventListener('focusin', focusin);
            document.body.style.overflow = overflow;
            for (const [node, original] of background) {
                if (original === null) node.removeAttribute('inert');
                else node.setAttribute('inert', original);
            }
            // Read the live fallback: its node may have been removed when the owning page retired.
            // eslint-disable-next-line react-hooks/exhaustive-deps
            const target = previous?.isConnected && !previous.matches(':disabled') ? previous : returnFallback?.current;
            if (target?.isConnected && !target.matches(':disabled')) target.focus();
        };
    }, [open, root, returnFallback]);
}
