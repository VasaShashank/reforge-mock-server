import { createMcpHandler } from "mcp-handler";
import { z } from "zod";

const handler = createMcpHandler((server) => {

  server.registerTool(
    "delhivery_check_pincode",
    {
      title: "Delhivery Pincode Serviceability",
      description:
        "Check whether a destination pincode is serviceable through the ReForge mock Delhivery service.",
      inputSchema: z.object({
        pin_code: z.string().regex(/^[0-9]{6}$/),
      }),
    },
    async ({ pin_code }) => {

      const response = await fetch(
        `https://reforge-mock-server.vercel.app/c/api/pin-codes/json/?filter_codes=${pin_code}`,
        {
          headers: {
            "X-API-Key": "reforge-demo-key",
          },
        }
      );

      const data = await response.json();

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(data),
          },
        ],
      };
    }
  );


  server.registerTool(
    "delhivery_create_shipment",
    {
      title: "Delhivery Create Shipment",
      description:
        "Create a Delhivery shipment for a machine-recovery spare part.",
      inputSchema: z.object({
        name: z.string(),
        add: z.string(),
        pin: z.string().regex(/^[0-9]{6}$/),
        city: z.string(),
        state: z.string(),
        country: z.string().default("India"),
        phone: z.string(),
        order: z.string(),
        payment_mode: z.string().default("Prepaid"),
        products_desc: z.string(),
        quantity: z.number().int().positive().default(1),
        weight: z.number().positive(),
        shipping_mode: z.string().default("Surface"),
      }),
    },
    async (input) => {

      const payload = {
        shipments: [
          {
            name: input.name,
            add: input.add,
            pin: input.pin,
            city: input.city,
            state: input.state,
            country: input.country,
            phone: input.phone,
            order: input.order,
            payment_mode: input.payment_mode,
            products_desc: input.products_desc,
            quantity: input.quantity,
            weight: input.weight,
            shipping_mode: input.shipping_mode,
          },
        ],
        pickup_location: {
          name: "REFORGE-WAREHOUSE",
        },
      };

      const response = await fetch(
        "https://reforge-mock-server.vercel.app/api/cmu/create.json",
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-API-Key": "reforge-demo-key",
          },
          body: JSON.stringify(payload),
        }
      );

      const data = await response.json();

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(data),
          },
        ],
      };
    }
  );


  server.registerTool(
    "delhivery_track_shipment",
    {
      title: "Delhivery Track Shipment",
      description:
        "Track a ReForge spare-parts shipment using its waybill or order ID.",
      inputSchema: z.object({
        waybill: z.string().optional(),
        order_id: z.string().optional(),
      }),
    },
    async ({ waybill, order_id }) => {

      const params = new URLSearchParams();

      if (waybill) {
        params.set("waybill", waybill);
      }

      if (order_id) {
        params.set("ref_ids", order_id);
      }

      const response = await fetch(
        `https://reforge-mock-server.vercel.app/api/v1/packages/json/?${params.toString()}`,
        {
          headers: {
            "X-API-Key": "reforge-demo-key",
          },
        }
      );

      const data = await response.json();

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(data),
          },
        ],
      };
    }
  );

});

export { handler as GET, handler as POST };
