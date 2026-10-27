import { createRouter, createWebHistory } from 'vue-router'

/**
 * 本系统只保留低频巡检一个页面，其它路由一并移除。
 */
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/sparse' },
    {
      path: '/sparse',
      name: 'sparse',
      component: () => import('@/views/SparseView.vue'),
      meta: { title: '低频巡检' }
    },
    { path: '/:pathMatch(.*)*', redirect: '/sparse' }
  ]
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || ''
  document.title = title ? `${title} · 摄像头篡改检测` : '摄像头篡改检测'
})

export default router
