<script setup lang="ts">
import ToastContainer from './components/ui/ToastContainer.vue'
</script>

<template>
  <div class="relative min-h-screen bg-[var(--bg-canvas)] text-[var(--text-primary)] antialiased">
    <ToastContainer />
    <router-view />
  </div>
</template>
