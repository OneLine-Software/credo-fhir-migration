import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'patient-list',
    component: () => import('@/views/PatientList.vue'),
  },
  {
    path: '/patient/:id',
    name: 'patient-detail',
    component: () => import('@/views/PatientDetail.vue'),
    props: true,
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
