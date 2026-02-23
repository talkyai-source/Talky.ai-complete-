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
    loading = false
}: ConfirmationModalProps) {
    const modalRef = useRef<HTMLDivElement>(null);
    const confirmButtonRef = useRef<HTMLButtonElement>(null);

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
            // Focus the confirm button when modal opens
            confirmButtonRef.current?.focus();
            // Prevent body scroll
            document.body.style.overflow = 'hidden';
        }

        return () => {
            document.removeEventListener('keydown', handleKeyDown);
            document.body.style.overflow = '';
        };
    }, [isOpen, handleKeyDown]);

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
                        disabled={loading}
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
