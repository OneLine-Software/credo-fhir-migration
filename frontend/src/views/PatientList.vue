<script setup>
import { useRouter } from 'vue-router'
import { usePatients } from '@/composables/usePatients'
import { useMigrationStatus } from '@/composables/useMigrationStatus'
import { formatDate, patientDisplayName, EM_DASH } from '@/lib/format'
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from '@/components/ui/table'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

const router = useRouter()

const {
  patients,
  loading,
  error,
  page,
  count,
  isEmpty,
  hasNextPage,
  hasPreviousPage,
  reload,
  nextPage,
  previousPage,
} = usePatients()

const { status: migrationStatus, unfinishedRun } = useMigrationStatus()

function goToPatient(id) {
  router.push({ name: 'patient-detail', params: { id } })
}
</script>

<template>
  <div class="container mx-auto max-w-5xl px-4 py-8">
    <div class="mb-6">
      <h1 class="text-2xl font-bold tracking-tight">Patients</h1>
      <p class="text-muted-foreground text-sm mt-1" v-if="migrationStatus">
        {{ migrationStatus.patients.toLocaleString() }} patients ·
        {{ migrationStatus.observations.toLocaleString() }} observations migrated
      </p>
      <p v-if="unfinishedRun" class="text-sm mt-2 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2">
        Last migration run is <strong>{{ unfinishedRun.status }}</strong> at patient offset
        {{ unfinishedRun.patients_offset.toLocaleString() }} — this list may be incomplete.
        Re-run <code class="bg-muted px-1 py-0.5 rounded">python manage.py migrate_fhir</code>
        to resume from the checkpoint.
      </p>
    </div>

    <!-- Error, loading, empty and loaded are mutually exclusive: showing the
         "run the migration" hint next to a failed request would be misleading. -->
    <div v-if="error" class="rounded-lg border border-destructive/50 bg-destructive/10 p-4">
      <p class="text-sm text-destructive">Failed to load patients: {{ error }}</p>
      <Button variant="outline" size="sm" class="mt-3" @click="reload">Try again</Button>
    </div>

    <div v-else-if="loading" class="text-muted-foreground py-12 text-center">
      Loading patients...
    </div>

    <div v-else-if="isEmpty" class="text-muted-foreground py-12 text-center">
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
            class="cursor-pointer focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
            role="link"
            tabindex="0"
            :aria-label="`View observations for ${patientDisplayName(patient)}`"
            @click="goToPatient(patient.fhir_id)"
            @keydown.enter="goToPatient(patient.fhir_id)"
            @keydown.space.prevent="goToPatient(patient.fhir_id)"
          >
            <TableCell class="font-medium">{{ patientDisplayName(patient) }}</TableCell>
            <TableCell class="font-mono text-xs text-muted-foreground">{{ patient.fhir_id }}</TableCell>
            <TableCell>
              <Badge v-if="patient.gender" variant="outline" class="capitalize">{{ patient.gender }}</Badge>
              <span v-else class="text-muted-foreground">{{ EM_DASH }}</span>
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
          <Button variant="outline" size="sm" :disabled="!hasPreviousPage" @click="previousPage">
            Previous
          </Button>
          <Button variant="outline" size="sm" :disabled="!hasNextPage" @click="nextPage">
            Next
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
