/**
 * TaskControlBar
 *
 * 任务控制条：DAG Progress / 文件预览 / 重置布局 / 进度百分比
 * 从原 ChatHeader 右侧抽出，独立放置在右侧 Preview 面板顶部
 *（与图二所示位置一致：顶部独立卡片，不与主 Header 混在一起）。
 */

import { memo } from 'react';

interface TaskControlBarProps {
  percent: number;
  isStreaming: boolean;
  previewOpen: boolean;
  onTaskClick: () => void;
  onTogglePreview?: () => void;
  onResetLayout?: () => void;
}

const TaskControlBar = memo(function TaskControlBar({
  percent,
  isStreaming,
  previewOpen,
  onTaskClick,
  onTogglePreview,
  onResetLayout,
}: TaskControlBarProps) {
  return (
    <div className="border-b border-warm-150 bg-white">
      <div className="px-4 py-2">
        <div className="mb-1.5 flex items-center justify-between text-caption text-warm-500">
          <div className="flex items-center gap-3">
            <button
              onClick={onTaskClick}
              className="text-primary-500 hover:text-primary-600 transition-colors"
            >
              DAG Progress / View Tasks
            </button>

            {onTogglePreview && (
              <button
                onClick={onTogglePreview}
                className={`text-sm font-medium transition-colors ${
                  previewOpen
                    ? 'text-accent-600 hover:text-accent-700'
                    : 'text-warm-500 hover:text-primary-600'
                }`}
                title={previewOpen ? '关闭预览面板' : '打开预览面板'}
              >
                {previewOpen ? '关闭预览' : '文件预览'}
              </button>
            )}

            {onResetLayout && (
              <>
                <span className="text-warm-200">|</span>
                <button
                  onClick={onResetLayout}
                  className="text-sm font-medium text-warm-500 transition-colors hover:text-primary-600"
                  title="重置侧栏 / 预览面板 / 文件树宽度到默认值"
                >
                  重置布局
                </button>
              </>
            )}
          </div>
          <span className={isStreaming ? 'text-primary-600 font-medium' : ''}>
            {percent}%
          </span>
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-warm-100">
          <div
            className="h-full bg-primary-500 transition-all duration-300"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>
    </div>
  );
});

export default TaskControlBar;
