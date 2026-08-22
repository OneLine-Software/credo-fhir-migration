<script setup>
import { cn } from '@/lib/utils'
import { buttonVariants } from './index.js'

const props = defineProps({
  variant: { type: String, required: false },
  size: { type: String, required: false },
  as: { type: String, required: false, default: 'button' },
  class: { type: null, required: false },
})
</script>

<template>
  <component :is="as" :class="cn(buttonVariants({ variant, size }), $props.class)">
    <slot />
  </component>
</template>
