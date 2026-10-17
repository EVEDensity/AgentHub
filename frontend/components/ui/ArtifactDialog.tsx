'use client';

import { useRef, type ReactNode, type RefObject } from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';

// Adapted from shadcn/ui dialog.tsx, MIT, commit
// c8c4027b6b192822ea2952b225ee4760cddedf0d, registry/default/ui/dialog.tsx.
// Full attribution and license: frontend/licenses/shadcn-ui.LICENSE.txt.
export interface ArtifactDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  returnFocusRef?: RefObject<HTMLElement>;
  children: ReactNode;
}

/** Presentation only: authorization and Artifact reads belong to the caller. */
export function ArtifactDialog({ open, onOpenChange, title, description, returnFocusRef, children }: ArtifactDialogProps) {
  const previousFocus = useRef<HTMLElement | null>(null);
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60" />
        <DialogPrimitive.Content
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[90vh] w-[min(96vw,1100px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded border border-warm-200 bg-warm-100 p-4 shadow-lg focus:outline-none md:p-5"
          onOpenAutoFocus={() => { previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const target = returnFocusRef?.current ?? previousFocus.current;
            if (target?.isConnected) target.focus();
          }}
        >
          <DialogPrimitive.Title className="pr-8 text-lg font-semibold text-warm-900">{title}</DialogPrimitive.Title>
          <DialogPrimitive.Description className="mt-1 pr-8 text-sm text-warm-600">{description}</DialogPrimitive.Description>
          <DialogPrimitive.Close aria-label="关闭产物预览" className="absolute right-3 top-3 rounded p-2 text-warm-600 hover:bg-warm-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-600">
            <X className="h-4 w-4" aria-hidden="true" />
          </DialogPrimitive.Close>
          <div className="mt-4 min-h-0 overflow-y-auto">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
