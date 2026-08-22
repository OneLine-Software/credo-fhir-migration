<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { fetchPatient } from '@/api'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ArrowLeft } from 'lucide-vue-next'

const props = defineProps({ id: String })
const router = useRouter()

const patient = ref(null)
const loading = ref(true)
const error = ref(null)

// Group observations by code_display for organized display
const groupedObservations = ref({})

async function loadPatient() {
  loading.value = true
  error.value = null
  try {
    const data = await fetchPatient(props.id)
    patient.value = data
    // Group observations by type (code_display or code_text)
    const grouped = {}
    for (const obs of data.observations || []) {
      const key = obs.code_display || obs.code_text || obs.code || 'Unknown'
      if (!grouped[key]) grouped[key] = []
      grouped[key].push(obs)
    }
    groupedObservations.value = grouped
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function formatDate(dateStr) {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

function formatDateTime(dateStr) {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleString('en-US', { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function formatValue(obs) {
  if (obs.value === null || obs.value === undefined) return '—'
  return `${obs.value}${obs.value_unit ? ' ' + obs.value_unit : ''}`
}

onMounted(loadPatient)
watch(() => props.id, loadPatient)
</script>

<template>
  <div class="container mx-auto max-w-4xl px-4 py-8">
    <Button variant="ghost" size="sm" class="mb-4" @click="router.push('/')">
      <ArrowLeft class="size-4" />
      Back to patients
    </Button>

    <div v-if="error" class="rounded-lg border border-destructive/50 bg-destructive/10 p-4 mb-4">
      <p class="text-sm text-destructive">Failed to load patient: {{ error }}</p>
    </div>

    <div v-if="loading" class="text-muted-foreground py-12 text-center">
      Loading patient...
    </div>

    <div v-else-if="patient">
      <Card class="mb-6">
        <CardHeader>
          <div class="flex items-center gap-3">
            <CardTitle class="text-xl">
              {{ patient.full_name || patient.family_name || 'Unknown' }}
            </CardTitle>
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
          <span class="text-muted-foreground font-normal text-sm">
            ({{ patient.observations?.length || 0 }})
          </span>
        </h2>
      </div>

      <div v-if="patient.observations?.length === 0" class="text-muted-foreground py-8 text-center">
        <p>No observations recorded for this patient.</p>
      </div>

      <div v-else class="space-y-4">
        <Card v-for="(obs, typeName) in groupedObservations" :key="typeName">
          <CardHeader class="pb-3">
            <CardTitle class="text-base">{{ typeName }}</CardTitle>
            <CardDescription>{{ obs.length }} {{ obs.length === 1 ? 'reading' : 'readings' }}</CardDescription>
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
                <TableRow v-for="o in obs" :key="o.fhir_id">
                  <TableCell class="text-muted-foreground">{{ formatDateTime(o.effective_date) }}</TableCell>
                  <TableCell class="font-medium font-mono">{{ formatValue(o) }}</TableCell>
                  <TableCell>
                    <Badge variant="outline" class="text-xs">{{ o.status }}</Badge>
                  </TableCell>
                  <TableCell class="text-muted-foreground capitalize">{{ o.category || '—' }}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  </div>
</template>
