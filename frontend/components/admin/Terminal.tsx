'use client';

import { type JSX } from 'react';
import EmptyState from '../ui/EmptyState';

export default function Terminal(): JSX.Element {
  return (
    <section className="card" role="status" aria-label="终端不可用">
      <EmptyState
        icon="›_"
        title="终端暂不可用"
        description="当前版本尚未接入真实终端服务，无法执行命令或读取系统、进程、文件及环境信息。"
      />
    </section>
  );
}
