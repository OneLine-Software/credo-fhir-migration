<script setup>
import { cn } from '@/lib/utils'

defineProps({
  class: { type: null, required: false },
})
</script>

<template>
  <thead :class="cn('[&_tr]:border-b', $props.class)">
    <slot />
  </thead>
</template>
