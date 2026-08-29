import { useEffect, useRef, useCallback } from 'react';
import { X, AlertTriangle, AlertCircle, Info, Loader2 } from 'lucide-react';
import './ConfirmationModal.css';

interface ConfirmationModalProps {
    isOpen: boolean;
    title: string;
    message: string;
    confirmLabel?: string;
    cancelLabel?: string;
    variant?: 'danger' | 'warning' | 'info';
    onConfirm: () => void;
    onCancel: () => void;
    loading?: boolean;
    reason?: string;
    onReasonChange?: (reason: string) => void;
    reasonLabel?: string;
    reasonPlaceholder?: string;
    reasonMinLength?: number;
}

export function ConfirmationModal({
    isOpen,
    title,
    message,
    confirmLabel = 'Confirm',
    cancelLabel = 'Cancel',
    variant = 'danger',
    onConfirm,
    onCancel,
    loading = false,
    reason = '',
    onReasonChange,
    reasonLabel = 'Reason for this action',
    reasonPlaceholder = 'Explain why this irreversible action is required',
    reasonMinLength = 8,
}: ConfirmationModalProps) {
    const modalRef = useRef<HTMLDivElement>(null);
    const confirmButtonRef = useRef<HTMLButtonElement>(null);
    const reasonInputRef = useRef<HTMLTextAreaElement>(null);
    const requiresReason = Boolean(onReasonChange);
    const normalizedReason = reason.trim();
    const reasonIsValid = !requiresReason
        || (normalizedReason.length >= reasonMinLength && /\p{L}/u.test(normalizedReason));

    // Handle Escape key
    const handleKeyDown = useCallback((e: KeyboardEvent) => {
        if (e.key === 'Escape' && !loading) {
            onCancel();
        }
    }, [onCancel, loading]);

    // Focus trap
    useEffect(() => {
        if (isOpen) {
            document.addEventListener('keydown', handleKeyDown);
            // Irreversible actions require a reason, so start at the evidence
            // field rather than putting a destructive button under focus.
            if (requiresReason) reasonInputRef.current?.focus();
            else confirmButtonRef.current?.focus();
            // Prevent body scroll
            document.body.style.overflow = 'hidden';
        }

        return () => {
            document.removeEventListener('keydown', handleKeyDown);
            document.body.style.overflow = '';
        };
    }, [isOpen, handleKeyDown, requiresReason]);

    // Handle backdrop click
    const handleBackdropClick = (e: React.MouseEvent) => {
        if (e.target === e.currentTarget && !loading) {
            onCancel();
        }
    };

    if (!isOpen) return null;

    const getIcon = () => {
        switch (variant) {
            case 'danger':
                return <AlertCircle className="modal-icon danger" />;
            case 'warning':
                return <AlertTriangle className="modal-icon warning" />;
            case 'info':
                return <Info className="modal-icon info" />;
            default:
                return <AlertCircle className="modal-icon danger" />;
        }
    };

    return (
        <div
            className="modal-backdrop"
            onClick={handleBackdropClick}
            role="dialog"
            aria-modal="true"
            aria-labelledby="modal-title"
        >
            <div
                className="modal-content"
                ref={modalRef}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="modal-header">
                    {getIcon()}
                    <h3 id="modal-title" className="modal-title">{title}</h3>
                    <button
                        className="modal-close"
                        onClick={onCancel}
                        disabled={loading}
                        aria-label="Close"
                    >
                        <X size={20} />
                    </button>
                </div>

                <div className="modal-body">
                    <p className="modal-message">{message}</p>
                    {onReasonChange && (
                        <label className="modal-reason-field">
                            <span>{reasonLabel}</span>
                            <textarea
                                ref={reasonInputRef}
                                value={reason}
                                onChange={(event) => onReasonChange(event.target.value)}
                                placeholder={reasonPlaceholder}
                                minLength={reasonMinLength}
                                maxLength={1000}
                                rows={3}
                                required
                                disabled={loading}
                                aria-describedby="modal-reason-help"
                            />
                            <small id="modal-reason-help">
                                {reasonIsValid
                                    ? 'This reason will be retained in the deletion audit.'
                                    : `Enter a meaningful reason of at least ${reasonMinLength} characters.`}
                            </small>
                        </label>
                    )}
                </div>

                <div className="modal-footer">
                    <button
                        className="btn btn-secondary"
                        onClick={onCancel}
                        disabled={loading}
                    >
                        {cancelLabel}
                    </button>
                    <button
                        ref={confirmButtonRef}
                        className={`btn btn-${variant}`}
                        onClick={onConfirm}
                        disabled={loading || !reasonIsValid}
                    >
                        {loading ? (
                            <>
                                <Loader2 className="animate-spin" size={16} />
                                Processing...
                            </>
                        ) : (
                            confirmLabel
                        )}
                    </button>
                </div>
            </div>
        </div>
    );
}
