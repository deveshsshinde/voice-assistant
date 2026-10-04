import java.util.Scanner;

import static java.lang.Math.PI;

public class Main{
    public static void main(String[] args){
        System.out.print("What is the radius of the circle:");
        Scanner input = new Scanner(System.in);
        Double radius = input.nextDouble();
        Double Circumference = 2 * PI * radius;
        System.out.println("The circumference of the circle of radius " +radius+ " is "+Circumference);


    }
}
