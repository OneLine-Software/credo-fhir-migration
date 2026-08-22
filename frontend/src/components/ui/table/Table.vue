<script setup>
import { cn } from '@/lib/utils'
import { tableVariants } from './index.js'

defineProps({
  class: { type: null, required: false },
})
</script>

<template>
  <div class="relative w-full overflow-auto">
    <table :class="cn(tableVariants(), $props.class)">
      <slot />
    </table>
  </div>
</template>
