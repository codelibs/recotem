import flushPromises from "flush-promises";
import axios from "axios";
import Vuetify from "vuetify";
import MockAdapter from "axios-mock-adapter";

import { paths } from "@/api/schema";
import { mount, createLocalVue } from "@vue/test-utils";
import { sleep } from "@/utils/index";
import { AuthModule } from "@/store/auth";
import SetupSplitConfig from "@/components/tuning_steps/SetupSplitConfig.vue";

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

const validPresetId = 42;

type SplitConfigs =
  paths["/api/split_config/"]["get"]["responses"]["200"]["content"]["application/json"];
mock.onGet(/\/api\/split_config\/\?.*$/g).reply(async (config) => {
  if (config.url?.match(/unnamed/g)) {
    return [
      200,
      [
        {
          id: validPresetId,
          name: "validPresetName",
          ins_datetime: "2021-01-01T01:23:45",
        },
      ] as SplitConfigs,
    ];
  } else if (config.url?.match(/.*name=existing.*$/g)) {
    return [
      200,
      [
        { id: 1, name: "existing", ins_datetime: "2021-01-01T01:23:45" },
      ] as SplitConfigs,
    ];
  } else {
    return [200, []];
  }
});

describe("SetupSplitConfig.vue", () => {
  const localVue = createLocalVue();
  const intersectionObserverMock = () => ({
    observe: () => null,
  });
  let vuetify: Vuetify;
  beforeEach(() => {
    vuetify = new Vuetify();
  });

  it("use default", async () => {
    const wrapper = mount(
      {
        template: `<div> <SetupSplitConfig v-model="result"  v-slot="{isValid}">
        <div validity>{{isValid}}</div>
        </SetupSplitConfig></div>`,
        data(): { result: any } {
          return {
            result: null,
          };
        },
        components: { SetupSplitConfig },
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

    await wrapper.find('input[name="use-preset"]').trigger("click");
    expect(wrapper.vm.$data.result).toStrictEqual(null);
    expect(wrapper.find("div[validity]").text()).toContain("false");

    expect(wrapper.find("tbody tr").text()).toContain("validPresetName");
    await wrapper.find("tbody tr .v-simple-checkbox").trigger("click");
    expect(wrapper.vm.$data.result).toBe(validPresetId);
    expect(wrapper.find("div[validity]").text()).toContain("true");

    await wrapper.find('input[name="manually-define"]').trigger("click");
    {
      // test  test_user_ratio
      for (let invalidValue of [-1.0, 1.1]) {
        await wrapper
          .find('input[name="test_user_ratio"]')
          .setValue(invalidValue);
        await flushPromises();
        await sleep(100);
        expect(wrapper.text()).toContain(
          "The value must be in the range [0.0, 1.0]"
        );
        expect(wrapper.find("div[validity]").text()).toBe("false");
      }
      await wrapper.find('input[name="test_user_ratio"]').setValue(0.25);
      await flushPromises();
      await sleep(100);
      expect(wrapper.text()).not.toContain(
        "The value must be in the range [0.0, 1.0]"
      );
      expect(wrapper.find("div[validity]").text()).toBe("true");
      expect(wrapper.vm.$data.result.test_user_ratio).toStrictEqual(0.25);
      await wrapper.find('input[name="test_user_ratio"]').setValue("");
    }

    {
      // test n_test-user
      for (let invalidValue of [-1.0, 1.1]) {
        await wrapper.find('input[name="n_test_users"]').setValue(invalidValue);
        await flushPromises();
        await sleep(100);
        expect(wrapper.text()).toContain(
          "The value must be a positive integer."
        );
        expect(wrapper.find("div[validity]").text()).toBe("false");
      }
      await wrapper.find('input[name="n_test_users"]').setValue(1.0);
      await flushPromises();
      await sleep(100);
      expect(wrapper.text()).not.toContain("The value must be a positive");
      expect(wrapper.find("div[validity]").text()).toBe("true");

      expect(wrapper.vm.$data.result.n_test_users).toStrictEqual(1.0);
    }

    {
      // test n_test-user
      for (let invalidValue of [-1.0, 1.1]) {
        await wrapper.find('input[name="n_test_users"]').setValue(invalidValue);
        await flushPromises();
        await sleep(100);
        expect(wrapper.text()).toContain(
          "The value must be a positive integer."
        );
        expect(wrapper.find("div[validity]").text()).toBe("false");
      }
      await wrapper.find('input[name="n_test_users"]').setValue(1.0);
      await flushPromises();
      await sleep(100);
      expect(wrapper.text()).not.toContain("The value must be a positive");
      expect(wrapper.find("div[validity]").text()).toBe("true");

      expect(wrapper.vm.$data.result.n_test_users).toStrictEqual(1.0);
    }

    {
      //test test_heldout_ratio
      for (let invalidValue of [-1.0, 1.1]) {
        await wrapper
          .find('input[name="test_heldout_ratio"]')
          .setValue(invalidValue);
        await flushPromises();
        await sleep(100);
        expect(wrapper.text()).toContain(
          "The value must be in the range [0.0, 1.0]."
        );
        expect(wrapper.find("div[validity]").text()).toBe("false");
      }
      await wrapper.find('input[name="test_heldout_ratio"]').setValue(0.75);
      await flushPromises();
      await sleep(100);
      expect(wrapper.text()).not.toContain("The value must be a positive");
      expect(wrapper.find("div[validity]").text()).toBe("true");

      expect(wrapper.vm.$data.result.heldout_ratio).toStrictEqual(0.75);
    }

    {
      // test n_test-user
      for (let invalidValue of [-1.0, 1.1]) {
        await wrapper
          .find('input[name="heldout_interactions"]')
          .setValue(invalidValue);
        await flushPromises();
        await sleep(100);
        expect(wrapper.text()).toContain(
          "The value must be a positive integer."
        );
        expect(wrapper.find("div[validity]").text()).toBe("false");
      }
      await wrapper.find('input[name="heldout_interactions"]').setValue(3.0);
      await flushPromises();
      await sleep(100);
      expect(wrapper.text()).not.toContain("The value must be a positive");
      expect(wrapper.find("div[validity]").text()).toBe("true");

      expect(wrapper.vm.$data.result.n_heldout).toStrictEqual(3.0);
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

    {
      // savename
      const expectedMessage = `A preset with this name already exists.`;
      await wrapper.find('input[name="savename"]').setValue("existin");
      await flushPromises();
      await sleep(500);
      expect(wrapper.text()).not.toContain(expectedMessage);
      expect(wrapper.find("div[validity]").text()).toBe("true");

      await wrapper.find('input[name="savename"]').setValue("existing");
      await flushPromises();
      await sleep(500);
      expect(wrapper.text()).toContain(expectedMessage);
      expect(wrapper.find("div[validity]").text()).toBe("false");

      await wrapper.find('input[name="savename"]').setValue("newPresetName");
      await flushPromises();
      await sleep(500);
      expect(wrapper.text()).not.toContain(expectedMessage);
      expect(wrapper.find("div[validity]").text()).toBe("true");

      expect(wrapper.vm.$data.result.name).toStrictEqual("newPresetName");
    }
  });
});
