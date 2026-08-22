import { cva } from 'class-variance-authority'

export { default as Table } from './Table.vue'
export { default as TableHeader } from './TableHeader.vue'
export { default as TableBody } from './TableBody.vue'
export { default as TableRow } from './TableRow.vue'
export { default as TableHead } from './TableHead.vue'
export { default as TableCell } from './TableCell.vue'

export const tableVariants = cva('w-full caption-bottom text-sm')
