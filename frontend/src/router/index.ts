import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/upload' },
    {
      path: '/upload',
      name: 'upload',
      component: () => import('@/views/UploadView.vue'),
      meta: { title: '媒体上传', icon: 'UploadFilled' }
    },
    {
      path: '/detect',
      name: 'detect',
      component: () => import('@/views/DetectView.vue'),
      meta: { title: '运行检测', icon: 'VideoPlay' }
    },
    {
      path: '/live',
      name: 'live',
      component: () => import('@/views/LiveView.vue'),
      meta: { title: '实时监控', icon: 'VideoCamera' }
    },
    {
      path: '/sparse',
      name: 'sparse',
      component: () => import('@/views/SparseView.vue'),
      meta: { title: '低频巡检', icon: 'Timer' }
    },
    {
      path: '/compare',
      name: 'compare',
      component: () => import('@/views/CompareView.vue'),
      meta: { title: '算法对比', icon: 'DataAnalysis' }
    },
    {
      path: '/analysis',
      name: 'analysis',
      component: () => import('@/views/AnalysisView.vue'),
      meta: { title: '结果分析', icon: 'TrendCharts' }
    },
    { path: '/:pathMatch(.*)*', redirect: '/upload' }
  ]
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || ''
  document.title = title ? `${title} · 摄像头篡改检测` : '摄像头篡改检测'
})

export default router
