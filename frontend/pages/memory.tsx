import { useEffect, type JSX } from 'react';
import { useRouter } from 'next/router';

export default function MemoryPage(): JSX.Element {
  const router = useRouter();

  useEffect(() => {
    void router.replace('/admin?menu=%E8%AE%B0%E5%BF%86');
  }, [router]);

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontFamily: 'system-ui, sans-serif',
      color: '#6b7280',
    }}>
      正在跳转到设置 / 记忆...
    </div>
  );
}
