export function findOrder(id: string): Order {
    return prisma.order.findUnique({ where: { id } });
}

export function saveOrder(order: Order): void {
    prisma.order.create({ data: order });
}
