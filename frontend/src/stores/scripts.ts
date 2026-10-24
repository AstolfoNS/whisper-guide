import { defineStore } from 'pinia'
import { deleteScript, getScript, listScripts, uploadScripts, type ScriptDocument, type ScriptSummary } from '../api/scripts'
import { useSessionStore } from './session'

export const useScriptsStore = defineStore('scripts', {
  state: () => ({
    scripts: [] as ScriptSummary[],
    selected: undefined as ScriptDocument | undefined,
    loading: false,
    deletingId: '',
    selectingId: '',
    error: '',
    request: 0,
    listRequest: 0,
  }),
  actions: {
    clearDeletedSelection(id: string) {
      const session = useSessionStore()
      if (session.active && session.snapshot?.script_id === id) return
      if (this.selected?.id === id) this.selected = undefined
      if (!session.active && session.selectedScript?.id === id) session.selectedScript = undefined
      if (this.selectingId === id) {
        this.request++
        this.selectingId = ''
      }
    },
    async refresh() {
      const request = ++this.listRequest
      this.loading = true
      try {
        const scripts = await listScripts(AbortSignal.timeout(10000))
        if (request !== this.listRequest) return
        this.scripts = scripts
        if (this.selected && !scripts.some(script => script.id === this.selected?.id)) {
          this.clearDeletedSelection(this.selected.id)
        }
        this.error = ''
      } catch (error) {
        if (request === this.listRequest) this.error = error instanceof Error ? error.message : '讲稿读取失败'
      } finally {
        if (request === this.listRequest) this.loading = false
      }
    },
    async select(id: string) {
      if (this.deletingId === id) return
      const request = ++this.request
      this.selectingId = id
      this.loading = true
      try {
        const script = await getScript(id, AbortSignal.timeout(10000))
        if (request === this.request && this.deletingId !== id) {
          this.selected = script
          this.error = ''
        }
      } catch (error) {
        if (request === this.request) this.error = error instanceof Error ? error.message : '讲稿读取失败'
      } finally {
        if (request === this.request) {
          this.loading = false
          this.selectingId = ''
        }
      }
    },
    async remove(id: string): Promise<boolean> {
      if (this.deletingId) return false
      const session = useSessionStore()
      if (session.active && session.snapshot?.script_id === id) {
        this.error = '讲稿正在会话中使用，请结束后再删除'
        return false
      }
      this.deletingId = id
      this.error = ''
      try {
        await deleteScript(id, AbortSignal.timeout(10000))
        this.clearDeletedSelection(id)
        await this.refresh()
        if (this.error) this.error = '讲稿已删除，但列表刷新失败，请点击刷新重试。'
        return true
      } catch (error) {
        this.error = error instanceof Error ? error.message : '删除讲稿失败，请重试。'
        return false
      } finally {
        this.deletingId = ''
      }
    },
    async upload(files: File[]) {
      if (!files.length || files.length > 10) throw new Error('每次选择 1–10 份讲稿。')
      if (files.some(file => !/\.(txt|text|md)$/i.test(file.name) || file.size === 0 || file.size > 2097152)) throw new Error('仅支持非空 txt、text、md，每份最多 2 MiB。')
      const created = await uploadScripts(files, AbortSignal.timeout(30000))
      await this.refresh()
      return created
    },
  },
})
