import { createRouter, createWebHistory } from 'vue-router'
import { nextTick } from 'vue'
import { getSessionController } from '../services/sessionController'

export const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', component: () => import('../views/DashboardView.vue'), meta: { title: '运行概览' } },
    { path: '/dashboard', redirect: '/' },
    { path: '/studio', component: () => import('../views/StudioView.vue'), meta: { title: '演讲工作台' } },
    { path: '/settings/models', component: () => import('../views/ModelConfigsView.vue'), meta: { title: '模型配置' } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
router.beforeEach(async (to, from) => {
  if (from.path === '/studio' && to.path !== '/studio') await getSessionController().leaveStudio()
  if (to.path === '/studio') getSessionController().enterStudio()
})
router.afterEach(async (to, from) => {
  document.title = `${String(to.meta.title ?? 'Whisper Guide')} · Whisper Guide`
  if (to.path !== from.path) {
    await nextTick()
    document.querySelector<HTMLElement>('#page-title')?.focus()
  }
})
