'use client';

import { memo } from 'react';
import { Modal } from '../ui/Modal';

interface OneClickDeployModalProps {
  open: boolean;
  sessionId: string;
  sessionName: string;
  onClose: () => void;
}

const OneClickDeployModal = memo(function OneClickDeployModal({ open, onClose }: OneClickDeployModalProps) {
  return (
    <Modal open={open} onClose={onClose} title="部署暂不可用">
      <div className="p-5 text-sm text-warm-600" role="status">
        当前版本尚未接入部署服务，无法生成部署链接、API 密钥、嵌入代码或安装包。
        请按仓库部署文档在受支持的环境中部署。
      </div>
    </Modal>
  );
});

export default OneClickDeployModal;
