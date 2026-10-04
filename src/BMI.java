import java.util.Scanner;
public class BMI{
    public static void main(String[] args){
        System.out.println("We are going to find the BMI of a person");
        System.out.print("Enter your body weight(kgs):");
        Scanner input = new Scanner(System.in);
        Double weight = input.nextDouble();
        System.out.print("Enter your Height(meters):");
        Double height = input.nextDouble();
        Double bmi = weight/(height*height);
        System.out.println("The BMI of your body given that:" + "\n Height = " + height + "\n weight =" + weight + "\n BMI = " +bmi);





    }
}
