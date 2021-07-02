package com.codecool.outputFormatter;

import java.util.ArrayList;

public class XMLOutput implements OutputFormatter{
    @Override
    public void printToConsole(ArrayList<String> data) {
        System.out.println("XMLOutput");
    }
}
