<script setup>
import { useRouter } from 'vue-router'
import { usePatient } from '@/composables/usePatient'
import {
  formatDate,
  formatDateTime,
  formatQuantity,
  patientDisplayName,
  EM_DASH,
} from '@/lib/format'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ArrowLeft } from 'lucide-vue-next'

const props = defineProps({ id: String })
const router = useRouter()

const { patient, loading, error, observationGroups, observationCount, load } = usePatient(
  () => props.id,
)
</script>

<template>
  <div class="container mx-auto max-w-4xl px-4 py-8">
    <Button variant="ghost" size="sm" class="mb-4" @click="router.push({ name: 'patient-list' })">
      <ArrowLeft class="size-4" />
      Back to patients
    </Button>

    <div v-if="error" class="rounded-lg border border-destructive/50 bg-destructive/10 p-4">
      <p class="text-sm text-destructive">Failed to load patient: {{ error }}</p>
      <Button variant="outline" size="sm" class="mt-3" @click="load">Try again</Button>
    </div>

    <div v-else-if="loading" class="text-muted-foreground py-12 text-center">
      Loading patient...
    </div>

    <div v-else-if="patient">
      <Card class="mb-6">
        <CardHeader>
          <div class="flex items-center gap-3">
            <CardTitle class="text-xl">{{ patientDisplayName(patient) }}</CardTitle>
            <Badge v-if="patient.gender" variant="outline" class="capitalize">{{ patient.gender }}</Badge>
            <Badge v-if="patient.active" variant="secondary">Active</Badge>
          </div>
          <CardDescription>
            FHIR ID: <span class="font-mono">{{ patient.fhir_id }}</span>
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div class="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p class="text-muted-foreground">Date of Birth</p>
              <p class="font-medium">{{ formatDate(patient.birth_date) }}</p>
            </div>
            <div v-if="patient.identifier_value">
              <p class="text-muted-foreground">Identifier</p>
              <p class="font-medium font-mono text-xs">{{ patient.identifier_value }}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <div class="mb-4">
        <h2 class="text-lg font-semibold tracking-tight">
          Observations
          <span class="text-muted-foreground font-normal text-sm">({{ observationCount }})</span>
        </h2>
      </div>

      <div v-if="!observationGroups.size" class="text-muted-foreground py-8 text-center">
        <p>No observations recorded for this patient.</p>
      </div>

      <div v-else class="space-y-4">
        <Card v-for="[typeName, observations] in observationGroups" :key="typeName">
          <CardHeader class="pb-3">
            <CardTitle class="text-base">{{ typeName }}</CardTitle>
            <CardDescription>
              {{ observations.length }} {{ observations.length === 1 ? 'reading' : 'readings' }}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Date</TableHead>
                  <TableHead>Value</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Category</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow v-for="observation in observations" :key="observation.fhir_id">
                  <TableCell class="text-muted-foreground">
                    {{ formatDateTime(observation.effective_date) }}
                  </TableCell>
                  <TableCell class="font-medium font-mono">
                    {{ formatQuantity(observation.value, observation.value_unit) }}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" class="text-xs">{{ observation.status }}</Badge>
                  </TableCell>
                  <TableCell class="text-muted-foreground capitalize">
                    {{ observation.category || EM_DASH }}
                  </TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  </div>
</template>
