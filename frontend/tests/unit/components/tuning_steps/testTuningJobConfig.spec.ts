import flushPromises from "flush-promises";
import axios from "axios";
import Vuetify from "vuetify";
import MockAdapter from "axios-mock-adapter";

import { mount, createLocalVue } from "@vue/test-utils";
import { sleep } from "@/utils/index";
import SetupTuningJob from "@/components/tuning_steps/SetupTuningJob.vue";

const mock = new MockAdapter(axios);
const createdDataId = 103;

mock.onPost("/api/auth/token/refresh/").reply(async () => {
  return [
    200,
    {
      access_token: "some token",
    },
  ];
});

describe("TuningJob.vue", () => {
  const localVue = createLocalVue();
  let vuetify: Vuetify;
  beforeEach(() => {
    vuetify = new Vuetify();
  });

  it("use default", async () => {
    const wrapper = mount(
      {
        template: `<div> <SetupTuningJob v-model="result"  v-slot="{isValid}">
        <div validity>{{isValid}}</div>
        </SetupTuningJob></div>`,
        data(): { result: any } {
          return {
            result: null,
          };
        },
        components: { SetupTuningJob },
      },
      {
        localVue,
        vuetify,
      }
    );
    await flushPromises();
    // default

    expect(wrapper.vm.$data.result).toStrictEqual({});
    expect(wrapper.find("div[validity]").text()).toContain("true");

    await wrapper.find('input[name="manually-define"]').trigger("click");
    for (let fieldName of [
      "n_trials",
      "timeout_overall",
      "timeout_singlestep",
      "memory_budget",
      "n_tasks_parallel",
    ]) {
      for (let invalidValue of [0.0, 1.1]) {
        await wrapper.find(`input[name="${fieldName}"]`).setValue(invalidValue);
        await flushPromises();
        await sleep(100);
        expect(wrapper.text()).toContain(
          "The value must be a positive integer."
        );
        expect(wrapper.find("div[validity]").text()).toBe("false");
      }
      await wrapper.find(`input[name="${fieldName}"]`).setValue(3.0);
      await flushPromises();
      await sleep(100);
      expect(wrapper.text()).not.toContain(
        "The value must be a positive integer."
      );
      expect(wrapper.find("div[validity]").text()).toBe("true");
      expect(wrapper.vm.$data.result[fieldName]).toStrictEqual(3.0);

      await wrapper.find(`input[name="${fieldName}"]`).setValue("");
      expect(wrapper.find("div[validity]").text()).toBe("true");
      expect(wrapper.vm.$data.result[fieldName]).toStrictEqual(undefined);
    }

    {
      // random_seed
      for (let invalidValue of [-1.0, 1.1]) {
        await wrapper.find('input[name="random_seed"]').setValue(invalidValue);
        await flushPromises();
        await sleep(100);
        expect(wrapper.text()).toContain(
          "The value must be a non-negative integer."
        );
        expect(wrapper.find("div[validity]").text()).toBe("false");
      }
      await wrapper.find('input[name="random_seed"]').setValue(0.0);
      await flushPromises();
      await sleep(100);
      expect(wrapper.text()).not.toContain(
        "The value must be a non-negative integer."
      );
      expect(wrapper.find("div[validity]").text()).toBe("true");

      expect(wrapper.vm.$data.result.random_seed).toStrictEqual(0.0);
    }
  });
});
