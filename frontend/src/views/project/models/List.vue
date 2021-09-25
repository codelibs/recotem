<template>
  <div>
    <v-container>
      <v-card>
        <v-card-title>
          <div class="pl-4 text-h6">Trained Models</div>
          <div class="flex-grow-1"></div>
          <div>
            <v-btn color="primary" :to="{ name: 'start-training' }">
              <v-icon>mdi-calculator</v-icon> Train new model</v-btn
            >
          </div>
        </v-card-title>
        <v-card-text>
          <ModelList />
        </v-card-text>
      </v-card>
    </v-container>
  </div>
</template>

<script lang="ts">
import Vue from "vue";
import ModelList from "@/components/ModelList.vue";
//import ModelConfigList from "@/components/ModelConfigList.vue";
export default Vue.extend({
  components: { ModelList },
  computed: {},
});
</script>
