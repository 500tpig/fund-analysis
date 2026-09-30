import { reactive } from 'vue'

export interface ToastItem {
  id: number
  type: 'success' | 'warning' | 'error' | 'info'
  content: string
}

let idCounter = 0
export const toastState = reactive<{ items: ToastItem[] }>({
  items: [],
})

export function showToast(content: string, type: ToastItem['type'] = 'info', duration = 2600) {
  const id = ++idCounter
  toastState.items.push({ id, type, content })

  setTimeout(() => {
    const idx = toastState.items.findIndex((item) => item.id === id)
    if (idx !== -1) {
      toastState.items.splice(idx, 1)
    }
  }, duration)
}

export function useMessage() {
  return {
    success: (msg: string) => showToast(msg, 'success'),
    warning: (msg: string) => showToast(msg, 'warning'),
    error: (msg: string) => showToast(msg, 'error'),
    info: (msg: string) => showToast(msg, 'info'),
  }
}
