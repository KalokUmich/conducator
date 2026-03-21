import { handleRequest, OrderController } from './handler';

describe('handleRequest', () => {
    it('should return order', () => {
        const req = { body: { orderId: '123' } };
        const result = handleRequest(req as any);
        expect(result.status).toBe(200);
    });

    it('should call findOrder', () => {
        jest.spyOn(orderRepo, 'findOrder');
        handleRequest({ body: { orderId: '1' } } as any);
        expect(orderRepo.findOrder).toHaveBeenCalledWith('1');
    });
});

describe('OrderController', () => {
    it('should get order by id', async () => {
        const ctrl = new OrderController();
        const result = await ctrl.getOrder('456');
        expect(result).toBeDefined();
    });
});
