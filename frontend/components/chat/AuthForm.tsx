import { memo } from 'react';
import type { AuthFormState } from '../../types';

interface AuthFormProps {
  authMode: 'login' | 'register';
  authForm: AuthFormState;
  notice: string;
  onSubmit: (e: React.FormEvent<HTMLFormElement>) => void;
  onToggleMode: () => void;
  onAuthFormChange: (update: Partial<AuthFormState>) => void;
}

const AuthForm = memo(function AuthForm({ authMode, authForm, notice, onSubmit, onToggleMode, onAuthFormChange }: AuthFormProps) {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <form onSubmit={onSubmit} className="card w-96 p-8">
        <h1 className="text-h1 text-warm-800">AgentHub {authMode === 'login' ? 'Login' : 'Register'}</h1>
        <p className="mt-2 text-caption text-warm-500">Use your bootstrap admin or registered account.</p>
        {notice && <div className="mt-4 bg-warning-50 p-3 text-sm text-warning-600">{notice}</div>}
        <label className="mt-6 block text-h4 text-warm-700">
          Username
          <input
            className="input-field mt-2"
            value={authForm.name}
            onChange={(e) => onAuthFormChange({ name: e.target.value })}
          />
        </label>
        <label className="mt-5 block text-h4 text-warm-700">
          Password
          <input
            type="password"
            className="input-field mt-2"
            value={authForm.password}
            onChange={(e) => onAuthFormChange({ password: e.target.value })}
          />
        </label>
        <button className="btn-primary mt-6 w-full">{authMode === 'login' ? 'Login' : 'Register'}</button>
        <button type="button" className="btn-ghost mt-3 w-full text-primary-500" onClick={onToggleMode}>
          {authMode === 'login' ? 'No account? Register' : 'Already have an account? Login'}
        </button>
      </form>
    </div>
  );
});

export default AuthForm;
