/** Admission currently accepts plain text; never silently discard selected input. */
export interface MissionChatInput {
  attachments?: readonly unknown[];
  references?: readonly unknown[];
  execPermission?: number;
}

export function unsupportedMissionInput(input: MissionChatInput): string | null {
  if (input.attachments?.length) return '当前 Mission 聊天暂不支持附件，请移除附件后发送文本。';
  if (input.references?.length) return '当前 Mission 聊天暂不支持文件或消息引用，请将需要的内容粘贴为文本。';
  if (input.execPermission != null && input.execPermission !== 1) return '当前 Mission 聊天暂不支持覆盖执行权限，权限由服务端合同控制。';
  return null;
}
