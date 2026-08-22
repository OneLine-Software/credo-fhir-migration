<script setup>
import { cn } from '@/lib/utils'

defineProps({
  class: { type: null, required: false },
})
</script>

<template>
  <td :class="cn('p-2 align-middle [&:has([role=checkbox])]:pr-0 [&>[role=checkbox]]:translate-y-[2px]', $props.class)">
    <slot />
  </td>
</template>
