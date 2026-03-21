import { findOrder } from './orderRepo';
import { sendNotification } from './notifier';

export function handleRequest(req: Request): Response {
    const orderId = req.body.orderId;
    const order = findOrder(orderId);
    sendNotification(order.userId, "order fetched");
    return { status: 200, data: order };
}

export class OrderController {
    async getOrder(id: string) {
        const result = findOrder(id);
        return result;
    }
}
