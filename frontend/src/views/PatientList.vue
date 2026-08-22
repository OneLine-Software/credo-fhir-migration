<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { fetchPatients, fetchMigrationStatus } from '@/api'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

const router = useRouter()
const patients = ref([])
const loading = ref(true)
const error = ref(null)
const page = ref(1)
const count = ref(0)
const nextUrl = ref(null)
const migrationStatus = ref(null)

async function loadPatients(targetPage = 1) {
  loading.value = true
  error.value = null
  try {
    const data = await fetchPatients(targetPage)
    patients.value = data.results
    count.value = data.count
    nextUrl.value = data.next
    page.value = targetPage
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadStatus() {
  try {
    migrationStatus.value = await fetchMigrationStatus()
  } catch {
    // silent fail — status is non-critical
  }
}

function goToPatient(id) {
  router.push({ name: 'patient-detail', params: { id } })
}

function formatDate(dateStr) {
  if (!dateStr) return '—'
  return new Date(dateStr).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
}

function displayName(patient) {
  return patient.full_name || patient.family_name || patient.fhir_id
}

onMounted(() => {
  loadPatients()
  loadStatus()
})
</script>

<template>
  <div class="container mx-auto max-w-5xl px-4 py-8">
    <div class="mb-6">
      <h1 class="text-2xl font-bold tracking-tight">Patients</h1>
      <p class="text-muted-foreground text-sm mt-1" v-if="migrationStatus">
        {{ migrationStatus.patients.toLocaleString() }} patients ·
        {{ migrationStatus.observations.toLocaleString() }} observations migrated
      </p>
    </div>

    <div v-if="error" class="rounded-lg border border-destructive/50 bg-destructive/10 p-4 mb-4">
      <p class="text-sm text-destructive">Failed to load patients: {{ error }}</p>
    </div>

    <div v-if="loading" class="text-muted-foreground py-12 text-center">
      Loading patients...
    </div>

    <div v-else-if="patients.length === 0" class="text-muted-foreground py-12 text-center">
      <p>No patients found. Run <code class="bg-muted px-1.5 py-0.5 rounded">python manage.py migrate_fhir</code> to populate the database.</p>
    </div>

    <div v-else>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>FHIR ID</TableHead>
            <TableHead>Gender</TableHead>
            <TableHead>Date of Birth</TableHead>
            <TableHead class="text-right">Observations</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow
            v-for="patient in patients"
            :key="patient.fhir_id"
            class="cursor-pointer"
            @click="goToPatient(patient.fhir_id)"
          >
            <TableCell class="font-medium">{{ displayName(patient) }}</TableCell>
            <TableCell class="font-mono text-xs text-muted-foreground">{{ patient.fhir_id }}</TableCell>
            <TableCell>
              <Badge v-if="patient.gender" variant="outline" class="capitalize">{{ patient.gender }}</Badge>
              <span v-else class="text-muted-foreground">—</span>
            </TableCell>
            <TableCell class="text-muted-foreground">{{ formatDate(patient.birth_date) }}</TableCell>
            <TableCell class="text-right">
              <span v-if="patient.observation_count > 0" class="font-medium">{{ patient.observation_count }}</span>
              <span v-else class="text-muted-foreground">0</span>
            </TableCell>
          </TableRow>
        </TableBody>
      </Table>

      <div class="flex items-center justify-between mt-6">
        <p class="text-sm text-muted-foreground">
          Page {{ page }} · {{ count.toLocaleString() }} total patients
        </p>
        <div class="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            :disabled="page === 1"
            @click="loadPatients(page - 1)"
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            :disabled="!nextUrl"
            @click="loadPatients(page + 1)"
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
