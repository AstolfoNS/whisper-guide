import { defineStore } from 'pinia'
import { getScript, listScripts, uploadScripts, type ScriptDocument, type ScriptSummary } from '../api/scripts'

export const useScriptsStore = defineStore('scripts', {
  state: () => ({ scripts: [] as ScriptSummary[], selected: undefined as ScriptDocument | undefined, loading: false, error: '', request: 0 }),
  actions: {
    async refresh() {
      this.loading = true
      try { this.scripts = await listScripts(AbortSignal.timeout(10000)); this.error = '' }
      catch (error) { this.error = error instanceof Error ? error.message : '讲稿读取失败' }
      finally { this.loading = false }
    },
    async select(id: string) {
      const request = ++this.request
      this.loading = true
      try {
        const script = await getScript(id, AbortSignal.timeout(10000))
        if (request === this.request) { this.selected = script; this.error = '' }
      } catch (error) { if (request === this.request) this.error = error instanceof Error ? error.message : '讲稿读取失败' }
      finally { if (request === this.request) this.loading = false }
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
