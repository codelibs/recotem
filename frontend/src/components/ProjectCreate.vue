<template>
  <v-card>
    <v-container grid-list-xs>
      <ValidationObserver v-slot="{ invalid }">
        <v-form>
          <ValidationProvider
            rules="required|projectNameExists"
            :debounce="500"
            name="project-name"
            v-slot="{ errors }"
          >
            <v-text-field
              type="text"
              name="project-name"
              v-model="project.name"
              label="name"
              :error-messages="errors"
            >
            </v-text-field>
          </ValidationProvider>
          <ValidationProvider
            rules="required"
            name="user column name"
            v-slot="{ errors }"
          >
            <v-text-field
              type="text"
              name="user column name"
              v-model="project.user_column"
              label="User column name"
              :error-messages="errors"
            >
            </v-text-field>
          </ValidationProvider>
          <ValidationProvider
            rules="required"
            name="item column name"
            v-slot="{ errors }"
          >
            <v-text-field
              type="text"
              name="Item column name"
              v-model="project.item_column"
              label="Item column name"
              :error-messages="errors"
            >
            </v-text-field>
          </ValidationProvider>
          <ValidationProvider name="time column name" v-slot="{ errors }">
            <v-text-field
              type="text"
              name="itime column name"
              v-model="project.time_column"
              label="(Optional) timestamp column name "
              :error-messages="errors"
            >
            </v-text-field>
          </ValidationProvider>
          <div class="text-center">
            <v-btn :disabled="invalid" @click="createProject" color="primary">
              Create new project</v-btn
            >
          </div>
        </v-form>
      </ValidationObserver>
    </v-container>
  </v-card>
</template>

<script lang="ts">
import Vue from "vue";
import qs from "qs";
import { components } from "@/api/schema";
import { postWithRefreshToken, getWithRefreshToken } from "@/utils";
import { AuthModule } from "@/store/auth";
import { required } from "vee-validate/dist/rules";
import { ValidationObserver, ValidationProvider, extend } from "vee-validate";
import { AxiosError } from "axios";

const ListProjectURL = "/api/project/";

extend("required", {
  ...required,
  message: "{_field_} required.",
});
extend("projectNameExists", {
  async validate(value: string) {
    const result = await getWithRefreshToken<Project[]>(
      AuthModule,
      ListProjectURL + `?${qs.stringify({ name: value })}`
    );
    if (result?.length === 0) {
      return true;
    }
    return `Project with this name already exists.`;
  },
});

type Project = components["schemas"]["Project"];
type ProjectData = Omit<
  Omit<Omit<Project, "ins_datetime">, "upd_datetime">,
  "id"
>;
type Data = {
  project: ProjectData;
};

export default Vue.extend({
  components: {
    ValidationProvider,
    ValidationObserver,
  },
  methods: {
    async createProject() {
      const result = await postWithRefreshToken<ProjectData, Project>(
        AuthModule,
        ListProjectURL,
        this.project
      ).catch((error: AxiosError) => {
        console.log(error.response?.data);
        alert("Uncaught error");
        return null;
      });
    },
  },
  data(): Data {
    return {
      project: {
        name: "",
        user_column: "",
        item_column: "",
        time_column: null,
      },
    };
  },
});
</script>
