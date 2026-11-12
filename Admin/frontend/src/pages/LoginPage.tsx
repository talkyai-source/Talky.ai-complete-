import { useEffect, useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { MFA_REQUIRED_MESSAGE, useAuth } from '../lib/auth';
import { safeAdminReturn } from '../lib/admin-routes';
import { TurnstileWidget } from '../components/TurnstileWidget';
import { Zap, Mail, Lock, AlertCircle, KeyRound } from 'lucide-react';

// Public build-time Cloudflare Turnstile site key. Unset: no script, widget or token.
const TURNSTILE_SITE_KEY = String(import.meta.env.VITE_TURNSTILE_SITE_KEY ?? '').trim();

export function LoginPage() {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [factor, setFactor] = useState('');
    const [recovery, setRecovery] = useState(false);
    const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
    const [turnstileResets, setTurnstileResets] = useState(0);
    const { login, verifyMfa, cancelMfa, mfaChallenge, error, isAuthenticated, isLoading, user, checkAuth, logout, logoutRecoveryRequired } = useAuth();
    const navigate = useNavigate();
    const location = useLocation();
    const target = safeAdminReturn((location.state as { from?: { pathname?: string } })?.from?.pathname, user?.role);
    useEffect(() => { if (isAuthenticated) navigate(target, { replace: true }); }, [isAuthenticated, target, navigate]);

    // With a site key a password sign-in waits for the widget's token; the MFA step needs none.
    const waitingForToken = TURNSTILE_SITE_KEY !== '' && !mfaChallenge && !turnstileToken;

    const submit = async (event: React.FormEvent) => {
        event.preventDefault();
        if (isLoading) return;
        if (mfaChallenge) await verifyMfa(factor, recovery);
        else if (!TURNSTILE_SITE_KEY) { await login(email, password); setPassword(''); }
        else if (turnstileToken) {
            // A Turnstile token is single-use: spend it once, then have the widget issue another.
            const token = turnstileToken;
            setTurnstileToken(null);
            await login(email, password, token);
            setPassword('');
            setTurnstileResets((count) => count + 1);
        }
        setFactor('');
    };
    return <div className="login-page"><div className="login-container">
        <div className="login-header">
            <div className="login-logo"><div className="login-logo-icon"><Zap size={24} /></div><span className="login-logo-text">Talk-lee</span></div>
            <h1>{mfaChallenge ? 'Verify your sign-in' : 'Admin Login'}</h1>
            <p>{mfaChallenge ? 'Complete the second factor for this account.' : 'Sign in to access the admin dashboard'}</p>
        </div>
        {error && <div className="login-error" role="alert"><AlertCircle size={16} /><span>{error}</span>
            {!mfaChallenge && error !== MFA_REQUIRED_MESSAGE && <button type="button" onClick={() => void (logoutRecoveryRequired ? logout() : checkAuth())} disabled={isLoading}>
                {logoutRecoveryRequired ? 'Retry browser sign-out' : 'Retry session check'}
            </button>}
        </div>}
        <form onSubmit={submit} className="login-form" aria-busy={isLoading}>
            {mfaChallenge ? <>
                <div className="form-group">
                    <label htmlFor="factor">{recovery ? 'Recovery code' : 'Authenticator code'}</label>
                    <div className="input-wrapper"><KeyRound size={18} /><input id="factor" type="text" value={factor}
                        onChange={(event) => setFactor(event.target.value)} inputMode={recovery ? 'text' : 'numeric'}
                        autoComplete="one-time-code" maxLength={recovery ? 128 : 6} required disabled={isLoading}
                        placeholder={recovery ? 'Enter the complete recovery code' : '000000'} /></div>
                </div>
                <button type="button" className="btn btn-secondary" disabled={isLoading}
                    onClick={() => { setRecovery(!recovery); setFactor(''); }}>
                    {recovery ? 'Use authenticator code' : 'Use a recovery code'}
                </button>
                <button type="button" className="btn btn-secondary" disabled={isLoading}
                    onClick={() => { cancelMfa(); setFactor(''); }}>Back to sign in</button>
            </> : <>
                <div className="form-group"><label htmlFor="email">Email</label><div className="input-wrapper"><Mail size={18} />
                    <input id="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)}
                        placeholder="admin@example.com" autoComplete="username" required disabled={isLoading} /></div></div>
                <div className="form-group"><label htmlFor="password">Password</label><div className="input-wrapper"><Lock size={18} />
                    <input id="password" type="password" value={password} onChange={(event) => setPassword(event.target.value)}
                        placeholder="Enter your password" autoComplete="current-password" required disabled={isLoading} /></div></div>
                {TURNSTILE_SITE_KEY !== '' && <TurnstileWidget siteKey={TURNSTILE_SITE_KEY} action="login"
                    onToken={setTurnstileToken} resetCount={turnstileResets} />}
            </>}
            <button type="submit" className="login-button" disabled={isLoading || waitingForToken}>{isLoading ? 'Verifying...' : mfaChallenge ? 'Verify' : 'Sign In'}</button>
        </form>
        <div className="login-footer"><p>Admin access only. Contact your administrator for access.</p></div>
    </div></div>;
}
